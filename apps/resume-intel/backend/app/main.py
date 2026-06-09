from __future__ import annotations

import html
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "/app/data/resume_intel.sqlite3"))
CV_TYPES_DIR = Path(os.getenv("CV_TYPES_DIR", "/workspace/output/cv_types"))
HH_RESUMES_PATH = Path(os.getenv("HH_RESUMES_PATH", "/app/config/hh_resumes.json"))

STOPWORDS = {
    "для",
    "или",
    "как",
    "что",
    "это",
    "где",
    "при",
    "под",
    "над",
    "the",
    "and",
    "with",
    "your",
    "you",
    "резюме",
    "вакансия",
    "компания",
    "обновлено",
    "импортировано",
    "файла",
    "current",
    "imported",
    "https",
    "www",
    "com",
    "ru",
    "москва",
    "россия",
}

TYPE_RECOMMENDATIONS = {
    "01-data-ai-platform-leader": [
        "Поднять выше Data & AI, DWH/Data Lake, Data Governance, MDM, modern data stack и управление инженерными/data-командами.",
        "Проверить, не выглядит ли резюме слишком sales/consulting-oriented для data/platform роли.",
        "Добавить больше конкретики про архитектуру данных, качество данных, витрины, lineage/catalog и безопасную работу с данными.",
    ],
    "02-ai-data-business-adoption-partner": [
        "Поднять выше AI/Data adoption: гипотезы, пилоты, ROI, метрики использования, AI-чемпионы и взаимодействие с ИТ/ИБ.",
        "Для бизнес-ролей ослабить R&D-лексикон и усилить процессы, бюджет, эффект и работу с владельцами процессов.",
        "Для инженерных ролей оставить Cursor, on-prem LLM, workflow, DORA/adoption-метрики и AI-амбассадоров.",
    ],
    "03-professional-it-consulting": [
        "Показать end-to-end consulting: as-is, to-be, reference architecture, roadmap, программа внедрения, эксплуатация и сопровождение.",
        "Не звучать как чистые продажи: account strategy и presale связывать с консалтингом, delivery и Data/AI-результатом.",
        "Поднять выше Астру, IBM, Teradata и Форсайт как доказательства client-facing enterprise consulting.",
    ],
}


class VacancyInput(BaseModel):
    company: str
    title: str
    url: str | None = None
    description: str


class NativeMailInput(BaseModel):
    subject: str = ""
    sender: str = ""
    sent_at: str = ""
    body: str
    raw_filename: str = "apple-mail-message.txt"


class ResumeImportResult(BaseModel):
    id: str
    title: str
    status: str
    channel: str
    url: str
    notes: str
    keywords: list[str]


app = FastAPI(title="Resume Intel", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5177", "http://127.0.0.1:5177"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                source TEXT NOT NULL,
                event_type TEXT NOT NULL,
                subject TEXT NOT NULL,
                sender TEXT NOT NULL,
                sent_at TEXT,
                company_name TEXT,
                resume_title TEXT,
                confidence REAL NOT NULL,
                raw_text TEXT NOT NULL,
                raw_filename TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS company_vacancies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                company_name TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT,
                description TEXT NOT NULL
            )
            """
        )


@app.on_event("startup")
def on_startup() -> None:
    init_db()


def strip_html(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<br\s*/?>", "\n", value)
    value = re.sub(r"(?s)</p\s*>", "\n", value)
    value = re.sub(r"(?s)<.*?>", " ", value)
    value = html.unescape(value)
    return normalize_text(value)


def normalize_text(value: str) -> str:
    value = value.replace("\u00a0", " ")
    value = re.sub(r"[\u200b-\u200f\u202a-\u202e\u2060\u2800\ufeff]", "", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def compact_lines(value: str) -> list[str]:
    return [line.strip() for line in normalize_text(value).splitlines() if line.strip()]


def looks_like_rfc822(value: str | bytes) -> bool:
    head = value[:5000] if isinstance(value, bytes) else value[:5000]
    if isinstance(head, bytes):
        lowered = head.lower()
        return b"\nsubject:" in lowered or b"\r\nsubject:" in lowered or lowered.startswith(b"received:")
    lowered = head.lower()
    return "\nsubject:" in lowered or "\r\nsubject:" in lowered or lowered.startswith("received:")


def extract_email_body(message: EmailMessage) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = part.get_content_disposition()
            if disposition == "attachment":
                continue
            try:
                content = part.get_content()
            except Exception:
                continue
            if content_type == "text/plain":
                plain_parts.append(str(content))
            elif content_type == "text/html":
                html_parts.append(strip_html(str(content)))
    else:
        content = message.get_content()
        if message.get_content_type() == "text/html":
            html_parts.append(strip_html(str(content)))
        else:
            plain_parts.append(str(content))

    body = "\n\n".join(part for part in plain_parts if part.strip())
    if not body:
        body = "\n\n".join(part for part in html_parts if part.strip())
    return normalize_text(body)


def decode_upload(filename: str, payload: bytes) -> dict[str, str]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".eml" or looks_like_rfc822(payload):
        message = message_from_bytes(payload, policy=default)
        subject = str(message.get("subject", "")).strip()
        sender = str(message.get("from", "")).strip()
        sent_at = str(message.get("date", "")).strip()
        body = extract_email_body(message)
        return {"subject": subject, "sender": sender, "sent_at": sent_at, "body": body}

    text = payload.decode("utf-8", errors="replace")
    if suffix in {".html", ".htm"} or re.search(r"<html|<body|<p\b", text, re.I):
        text = strip_html(text)
    return {"subject": first_subject_line(text), "sender": "", "sent_at": "", "body": normalize_text(text)}


def strip_rtf(value: str) -> str:
    value = re.sub(r"\\'[0-9a-fA-F]{2}", " ", value)
    value = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", value)
    value = re.sub(r"[{}]", " ", value)
    return normalize_text(value)


def extract_pdf_text(payload: bytes) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail="Для импорта PDF нужна backend-зависимость pypdf. Пересоберите backend-контейнер после обновления requirements.txt.",
        ) from exc

    try:
        reader = PdfReader(BytesIO(payload))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Не удалось прочитать PDF: {exc}") from exc
    return normalize_text(text)


def decode_resume_upload(filename: str, payload: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(payload)

    text = payload.decode("utf-8", errors="replace")
    if suffix in {".html", ".htm"} or re.search(r"<html|<body|<p\b", text, re.I):
        return strip_html(text)
    if suffix == ".rtf" or text.lstrip().startswith("{\\rtf"):
        return strip_rtf(text)
    return normalize_text(text)


def first_subject_line(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:180]
    return ""


def first_content_line(text: str) -> str:
    ignored = {
        "резюме",
        "curriculum vitae",
        "hh.ru",
        "headhunter",
        "мои резюме",
    }
    for line in compact_lines(text):
        cleaned = normalize_entity(line)
        if not cleaned:
            continue
        if cleaned.lower() in ignored:
            continue
        if len(cleaned) > 140:
            continue
        return cleaned
    return ""


def extract_hh_resume_external_id(*values: str) -> str:
    value = "\n".join(item for item in values if item)
    patterns = [
        r"hh\.ru/resume/([A-Za-z0-9_-]{8,})",
        r"hh\.ru/applicant/resumes/([A-Za-z0-9_-]{8,})",
        r"(?:resume_id|resumeId|resume)\s*[:=]\s*([A-Za-z0-9_-]{8,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, re.I)
        if match:
            return match.group(1)
    return ""


def split_markdown_sections(content: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    buffer: list[str] = []

    for line in content.splitlines():
        match = re.match(r"^(#{1,4})\s+(.+?)\s*$", line)
        if match:
            if current:
                current["content"] = normalize_text("\n".join(buffer))
                current["bullets"] = extract_bullets(current["content"])
                sections.append(current)
            current = {
                "level": len(match.group(1)),
                "title": normalize_entity(match.group(2)),
            }
            buffer = []
        else:
            buffer.append(line)

    if current:
        current["content"] = normalize_text("\n".join(buffer))
        current["bullets"] = extract_bullets(current["content"])
        sections.append(current)

    return sections


def extract_bullets(content: str) -> list[str]:
    bullets = []
    for line in content.splitlines():
        match = re.match(r"^\s*[-*]\s+(.+)$", line)
        if match:
            bullets.append(normalize_entity(match.group(1)))
    return bullets


def section_slug(title: str) -> str:
    lowered = title.lower()
    if any(value in lowered for value in ["профиль", "о себе", "summary", "about"]):
        return "summary"
    if any(value in lowered for value in ["компетен", "навык", "skills", "требован"]):
        return "skills"
    if any(value in lowered for value in ["опыт", "experience", "достижен"]):
        return "experience"
    if any(value in lowered for value in ["образован", "education"]):
        return "education"
    if any(value in lowered for value in ["сертифик", "обучен", "курсы"]):
        return "certifications"
    if any(value in lowered for value in ["язык", "languages"]):
        return "languages"
    if any(value in lowered for value in ["риски", "risk"]):
        return "risks"
    if any(value in lowered for value in ["кейсы", "cases"]):
        return "source_cases"
    if any(value in lowered for value in ["cover", "сопровод"]):
        return "cover_letter"
    if any(value in lowered for value in ["интерв", "interview"]):
        return "interview"
    return "other"


def enrich_markdown_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    parent_kind = "other"
    for section in sections:
        kind = section_slug(section["title"])
        if kind == "other" and parent_kind != "other" and section.get("level", 1) > 2:
            kind = parent_kind
        if section.get("level", 1) <= 2 and kind != "other":
            parent_kind = kind
        enriched.append({**section, "kind": kind})
    return enriched


def split_plain_resume_sections(text: str) -> dict[str, str]:
    aliases = {
        "summary": ["о себе", "обо мне", "профессиональный профиль", "summary", "about"],
        "skills": ["ключевые навыки", "навыки", "skills", "компетенции"],
        "experience": ["опыт работы", "профессиональный опыт", "work experience", "experience"],
        "education": ["образование", "education"],
        "certifications": ["повышение квалификации", "курсы", "сертификаты", "дополнительное обучение"],
        "languages": ["знание языков", "языки", "languages"],
    }
    line_to_key: dict[str, str] = {}
    for key, names in aliases.items():
        for name in names:
            line_to_key[name] = key

    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in compact_lines(text):
        normalized = line.lower().strip(" .:")
        for heading, key in line_to_key.items():
            if normalized == heading or normalized.startswith(f"{heading} "):
                current = key
                sections.setdefault(current, [])
                break
        else:
            sections.setdefault(current, []).append(line)
            continue

    return {key: normalize_text("\n".join(lines)) for key, lines in sections.items() if lines}


def split_skills(value: str) -> list[str]:
    skills = []
    for part in re.split(r"[,;•\n]+", value):
        cleaned = normalize_entity(part)
        if 2 < len(cleaned) <= 80:
            skills.append(cleaned)
    return list(dict.fromkeys(skills))


def looks_like_period(line: str) -> bool:
    months = (
        "январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август",
        "сентябр", "октябр", "ноябр", "декабр", "january", "february",
        "march", "april", "may", "june", "july", "august", "september",
        "october", "november", "december",
    )
    lowered = line.lower()
    return bool(re.search(r"(19|20)\d{2}", lowered)) and ("—" in line or "-" in line or any(month in lowered for month in months))


def looks_like_duration(line: str) -> bool:
    lowered = line.lower()
    return bool(re.search(r"\d+\s+(год|года|лет|месяц|месяца|месяцев|year|years|month|months)", lowered))


def parse_experience_entries(experience_text: str) -> list[dict[str, Any]]:
    lines = compact_lines(experience_text)
    period_indexes = [idx for idx, line in enumerate(lines) if looks_like_period(line) and ("—" in line or "-" in line)]
    if period_indexes:
        entries: list[dict[str, Any]] = []
        for order, period_idx in enumerate(period_indexes):
            next_period_idx = period_indexes[order + 1] if order + 1 < len(period_indexes) else len(lines)
            company_start = 0 if order == 0 else period_indexes[order - 1] + 1
            company_candidates = []
            for idx in range(period_idx - 1, company_start - 1, -1):
                line = lines[idx]
                if line.startswith("-"):
                    break
                if looks_like_duration(line):
                    continue
                company_candidates.append(line)
            company_candidates = list(reversed(company_candidates))

            period_parts = [lines[period_idx]]
            cursor = period_idx + 1
            if cursor < len(lines) and re.search(r"(19|20)\d{2}|настоящее|present", lines[cursor], re.I):
                period_parts.append(lines[cursor])
                cursor += 1
            if cursor < len(lines) and looks_like_duration(lines[cursor]):
                cursor += 1

            position = lines[cursor] if cursor < len(lines) else ""
            cursor += 1
            body = lines[cursor:next_period_idx]
            while body and not body[-1].startswith("-"):
                body.pop()
            description = normalize_text("\n".join(body))

            entries.append(
                {
                    "period": normalize_text(" ".join(period_parts)),
                    "company": normalize_entity(company_candidates[0]) if company_candidates else "",
                    "position": normalize_entity(position),
                    "description": description,
                    "achievements": extract_bullets(description),
                }
            )
        return entries

    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    body: list[str] = []

    for line in lines:
        if looks_like_period(line):
            if current:
                current["description"] = normalize_text("\n".join(body))
                current["achievements"] = extract_bullets(current["description"])
                entries.append(current)
            current = {"period": line, "company": "", "position": "", "description": "", "achievements": []}
            body = []
            continue

        if current and not current["company"]:
            current["company"] = normalize_entity(line)
            continue
        if current and not current["position"]:
            current["position"] = normalize_entity(line)
            continue
        if current:
            body.append(line)

    if current:
        current["description"] = normalize_text("\n".join(body))
        current["achievements"] = extract_bullets(current["description"])
        entries.append(current)

    return entries


def parse_resume_structure(text: str, title: str) -> dict[str, Any]:
    sections = split_plain_resume_sections(text)
    header_lines = compact_lines(sections.get("header", ""))[:12]
    summary = sections.get("summary", "")
    skills_text = sections.get("skills", "")
    experience_text = sections.get("experience", "")
    education_text = sections.get("education", "")
    certifications_text = sections.get("certifications", "")
    languages_text = sections.get("languages", "")

    return {
        "title": title,
        "header": header_lines,
        "summary": summary,
        "skills": split_skills(skills_text),
        "experience": parse_experience_entries(experience_text),
        "education": extract_bullets(education_text) or compact_lines(education_text),
        "certifications": extract_bullets(certifications_text) or compact_lines(certifications_text),
        "languages": extract_bullets(languages_text) or compact_lines(languages_text),
        "sections": [
            {"key": key, "title": key, "content": value}
            for key, value in sections.items()
            if key != "header" and value
        ],
        "parser_notes": [
            "Эвристический разбор по заголовкам и периодам; исходный текст сохранен полностью в raw_text.",
        ],
    }


def detect_source(subject: str, sender: str, text: str) -> str:
    value = f"{subject}\n{sender}\n{text[:1000]}".lower()
    if "hh.ru" in value or "headhunter" in value or "хэдхантер" in value:
        return "hh"
    if "linkedin" in value:
        return "linkedin"
    return "unknown"


def detect_event_type(subject: str, text: str) -> str:
    value = f"{subject}\n{text[:1500]}".lower()
    if "привлекло внимание" in value or "просмотр" in value or "посмотрел" in value:
        return "resume_attention"
    if "подходящие вакансии" in value or "recommended jobs" in value:
        return "recommended_jobs"
    if "отклик" in value or "приглашение" in value or "interview" in value:
        return "recruiter_message"
    return "unknown"


def find_first(patterns: list[str], value: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, value, re.I | re.M)
        if match:
            return normalize_entity(match.group(1))
    return None


def normalize_entity(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"[«»\"']", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" .,:;—-")


def is_bad_company_candidate(value: str) -> bool:
    lowered = value.lower()
    return (
        lowered.startswith(("received:", "subject:", "from:", "dkim-signature:", "content-type:"))
        or "mail.yandex.net" in lowered
        or "hh.ru" == lowered
        or "headhunter" in lowered
        or "хэдхантер" in lowered
        or len(value) > 120
    )


def extract_hh_resume_company(text: str) -> tuple[str | None, str | None]:
    lines = compact_lines(text)
    start_idx = 0
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if "ваши резюме" in lowered and "просматрив" in lowered:
            start_idx = idx
            break

    skip_fragments = {
        "письмо",
        "96",
        "вот кто его смотрел",
        "посмотрите открытые",
        "вакансии",
        "посмотрите вакансии, которые у них открыты",
        "возможно, среди них найдутся подходящие для вас",
        "если нужна помощь",
        "написать в поддержку",
        "управлять рассылкой",
        "оставайтесь на связи",
    }

    for idx, line in enumerate(lines[start_idx:], start=start_idx):
        if "обратите внимание" not in line.lower():
            continue
        candidates: list[str] = []
        for previous in reversed(lines[start_idx:idx]):
            normalized = previous.lower().strip(" .,:;")
            if normalized in skip_fragments:
                continue
            if "ваши резюме" in normalized or "просматрив" in normalized:
                continue
            if len(previous) < 3 or len(previous) > 100:
                continue
            candidates.append(normalize_entity(previous))
            if len(candidates) == 2:
                break
        if len(candidates) >= 2 and not is_bad_company_candidate(candidates[0]):
            return candidates[0], candidates[1]

    quoted_company_indexes: list[tuple[int, str]] = []
    for idx, line in enumerate(lines[start_idx:], start=start_idx):
        for company in re.findall(r"«([^»]+)»", line):
            company = normalize_entity(company)
            if company and not is_bad_company_candidate(company):
                quoted_company_indexes.append((idx, company))

    if not quoted_company_indexes:
        return None, None

    company_idx, company = quoted_company_indexes[0]
    resume_title = None
    for line in reversed(lines[max(0, company_idx - 8):company_idx]):
        normalized = line.lower().strip(" .,:;")
        if normalized in skip_fragments:
            continue
        if "ваши резюме" in normalized or "просматривала" in normalized:
            continue
        if len(line) < 4 or len(line) > 100:
            continue
        resume_title = normalize_entity(line)
        break

    return company, resume_title


def extract_company(subject: str, text: str) -> tuple[str | None, float]:
    hh_company, _ = extract_hh_resume_company(text)
    if hh_company:
        return hh_company, 0.9

    value = f"{subject}\n{text}"
    company = find_first(
        [
            r"резюме привлекло внимание(?: компании| работодателя)?\s+[«\"]?([^\"»\n]+)",
            r"компания\s+[«\"]?([^\"»\n]+)[»\"]?\s+(?:просмотрела|заинтересовалась|обратила внимание)",
            r"работодатель\s+[«\"]?([^\"»\n]+)[»\"]?\s+(?:просмотрел|заинтересовался|обратил внимание)",
            r"([A-ZА-ЯЁ0-9][^\n]{2,80})\s+(?:просмотрела|просмотрел)\s+ваше резюме",
            r"Компания:\s*([^\n]+)",
            r"Работодатель:\s*([^\n]+)",
        ],
        value,
    )
    generic_subjects = {
        "вчера ваше резюме привлекло внимание",
        "ваше резюме привлекло внимание",
        "сегодня ваше резюме привлекло внимание",
    }
    if company and company.lower() not in generic_subjects and not is_bad_company_candidate(company):
        return company, 0.85
    return None, 0.25


def extract_resume_title(subject: str, text: str) -> tuple[str | None, float]:
    _, hh_resume = extract_hh_resume_company(text)
    if hh_resume:
        return hh_resume, 0.9

    value = f"{subject}\n{text}"
    title = find_first(
        [
            r"резюме\s+[«\"]([^\"»\n]+)[»\"]",
            r"резюме:\s*([^\n]+)",
            r"Resume:\s*([^\n]+)",
            r"CV:\s*([^\n]+)",
        ],
        value,
    )
    if title:
        return title, 0.8
    return None, 0.2


def parse_upload(filename: str, payload: bytes) -> dict[str, Any]:
    decoded = decode_upload(filename, payload)
    subject = decoded["subject"]
    sender = decoded["sender"]
    body = decoded["body"]
    source = detect_source(subject, sender, body)
    event_type = detect_event_type(subject, body)
    company, company_confidence = extract_company(subject, body)
    resume_title, resume_confidence = extract_resume_title(subject, body)
    confidence = max(0.1, round((company_confidence + resume_confidence) / 2, 2))
    return {
        "source": source,
        "event_type": event_type,
        "subject": subject,
        "sender": sender,
        "sent_at": decoded["sent_at"],
        "company_name": company,
        "resume_title": resume_title,
        "confidence": confidence,
        "raw_text": body,
        "raw_filename": filename,
    }


def is_subject_only_mail_hint(parsed: dict[str, Any]) -> bool:
    raw_text = parsed.get("raw_text", "").strip()
    subject = parsed.get("subject", "").strip()
    return (
        len(raw_text) < 120
        and raw_text == subject
        and parsed.get("event_type") == "resume_attention"
        and not parsed.get("company_name")
        and not parsed.get("resume_title")
    )


def tokens(value: str) -> set[str]:
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9+/#.-]{2,}", value.lower())
    result = set()
    for word in words:
        cleaned = word.strip(".,:;/#-+")
        if cleaned in STOPWORDS or len(cleaned) <= 2:
            continue
        if cleaned.isdigit() or re.fullmatch(r"\d+[a-zа-яё.-]*", cleaned):
            continue
        if re.fullmatch(r"(19|20)\d{2}", cleaned) or re.search(r"\d{2,}", cleaned):
            continue
        if "." in cleaned and not any(char in cleaned for char in ["+", "#"]):
            continue
        result.add(cleaned)
    return result


def load_cv_types() -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if not CV_TYPES_DIR.exists():
        return items
    for folder in sorted(path for path in CV_TYPES_DIR.iterdir() if path.is_dir()):
        skills_path = folder / "skills_requirements.md"
        analysis_path = folder / "analysis.md"
        cv_path = folder / "tailored_cv.md"
        content = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in [skills_path, analysis_path, cv_path]
            if path.exists()
        )
        title = folder.name
        headline_match = re.search(r"\*\*([^*]+)\*\*", cv_path.read_text(encoding="utf-8", errors="replace") if cv_path.exists() else "")
        if headline_match:
            title = headline_match.group(1)
        items.append({"slug": folder.name, "title": title, "content": content})
    return items


def load_cv_type_detail(slug: str) -> dict[str, Any] | None:
    folder = CV_TYPES_DIR / slug
    if not folder.exists() or not folder.is_dir():
        return None

    documents = []
    for filename, title in [
        ("analysis.md", "Анализ"),
        ("skills_requirements.md", "Навыки и требования"),
        ("tailored_cv.md", "Tailored CV"),
        ("cover_letter_template.md", "Шаблон cover letter"),
        ("interview_prep.md", "Подготовка к интервью"),
        ("source_cases.md", "Исходные кейсы"),
    ]:
        path = folder / filename
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace")
            sections = enrich_markdown_sections(split_markdown_sections(content))
            documents.append(
                {
                    "filename": filename,
                    "title": title,
                    "content": content,
                    "sections": sections,
                }
            )

    title = slug
    tailored = next((doc["content"] for doc in documents if doc["filename"] == "tailored_cv.md"), "")
    headline_match = re.search(r"\*\*([^*]+)\*\*", tailored)
    if headline_match:
        title = headline_match.group(1)

    content = "\n".join(doc["content"] for doc in documents)
    return {
        "slug": slug,
        "title": title,
        "documents": documents,
        "structure": {
            "documents": [
                {
                    "filename": doc["filename"],
                    "title": doc["title"],
                    "sections": doc["sections"],
                }
                for doc in documents
            ],
            "section_index": [
                {
                    "document": doc["filename"],
                    "document_title": doc["title"],
                    "title": section["title"],
                    "kind": section["kind"],
                    "bullets": section["bullets"],
                }
                for doc in documents
                for section in doc["sections"]
            ],
        },
        "keywords": sorted(tokens(content))[:120],
    }


def load_hh_resumes() -> list[dict[str, str]]:
    if not HH_RESUMES_PATH.exists():
        return []
    try:
        data = json.loads(HH_RESUMES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    items = data.get("resumes", data if isinstance(data, list) else [])
    result = []
    for item in items:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        content = "\n".join(
            str(item.get(key, ""))
            for key in ["title", "keywords", "raw_text"]
        )
        result.append(
            {
                "id": str(item.get("id", title)),
                "title": title,
                "status": str(item.get("status", "current_hh")),
                "channel": str(item.get("channel", "hh")),
                "external_id": str(item.get("external_id", "")),
                "url": str(item.get("url", "")),
                "notes": str(item.get("notes", "")),
                "source_filename": str(item.get("source_filename", "")),
                "updated_at": str(item.get("updated_at", item.get("imported_at", ""))),
                "content": content,
            }
        )
    return result


def save_hh_resume(item: dict[str, Any]) -> dict[str, Any]:
    HH_RESUMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"resumes": []}
    if HH_RESUMES_PATH.exists():
        try:
            loaded = json.loads(HH_RESUMES_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
            elif isinstance(loaded, list):
                data = {"resumes": loaded}
        except json.JSONDecodeError:
            data = {"resumes": []}

    resumes = data.setdefault("resumes", [])
    resumes = [resume for resume in resumes if str(resume.get("id")) != item["id"]]
    resumes.append(item)
    data["resumes"] = resumes
    HH_RESUMES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return item


def find_hh_resume_by_external_id(external_id: str) -> dict[str, Any] | None:
    if not external_id or not HH_RESUMES_PATH.exists():
        return None
    try:
        data = json.loads(HH_RESUMES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    items = data.get("resumes", data if isinstance(data, list) else [])
    for item in items:
        if str(item.get("external_id", "")) == external_id:
            return item
    return None


def find_hh_resume_by_id(resume_id: str) -> dict[str, Any] | None:
    if not resume_id or not HH_RESUMES_PATH.exists():
        return None
    try:
        data = json.loads(HH_RESUMES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    items = data.get("resumes", data if isinstance(data, list) else [])
    for item in items:
        if str(item.get("id", "")) == resume_id:
            return item
    return None


def hh_resume_detail_payload(item: dict[str, Any]) -> dict[str, Any]:
    content = "\n".join(
        str(item.get(key, ""))
        for key in ["title", "keywords", "raw_text"]
    )
    parsed_structure = item.get("parsed_structure") or parse_resume_structure(
        str(item.get("raw_text", "")),
        str(item.get("title", "")),
    )
    return {
        "id": str(item.get("id", "")),
        "title": str(item.get("title", "")),
        "status": str(item.get("status", "current_hh")),
        "channel": str(item.get("channel", "hh")),
        "external_id": str(item.get("external_id", "")),
        "url": str(item.get("url", "")),
        "notes": str(item.get("notes", "")),
        "source_filename": str(item.get("source_filename", "")),
        "created_at": str(item.get("created_at", "")),
        "updated_at": str(item.get("updated_at", item.get("imported_at", ""))),
        "import_count": int(item.get("import_count", 0) or 0),
        "raw_text": str(item.get("raw_text", "")),
        "parsed_structure": parsed_structure,
        "keywords": resume_keywords(item, parsed_structure, content, 120),
    }


def resume_keywords(
    item: dict[str, Any],
    parsed_structure: dict[str, Any] | None = None,
    content: str | None = None,
    limit: int = 80,
) -> list[str]:
    structure = parsed_structure or item.get("parsed_structure") or {}
    skills = [str(skill).strip() for skill in structure.get("skills", []) if str(skill).strip()]
    if skills:
        return skills[:limit]
    source = content if content is not None else "\n".join(str(item.get(key, "")) for key in ["title", "keywords", "raw_text"])
    return sorted(tokens(source))[:limit]


def vacancy_text_for_company(company_name: str | None) -> str:
    if not company_name:
        return ""
    with connect() as conn:
        rows = conn.execute(
            "SELECT title, description FROM company_vacancies WHERE lower(company_name) = lower(?) ORDER BY id DESC",
            (company_name,),
        ).fetchall()
    return "\n".join(f"{row['title']}\n{row['description']}" for row in rows)


def match_cv_types(event: sqlite3.Row | dict[str, Any]) -> list[dict[str, Any]]:
    source_text = "\n".join(
        [
            str(event.get("subject", "") if isinstance(event, dict) else event["subject"] or ""),
            str(event.get("company_name", "") if isinstance(event, dict) else event["company_name"] or ""),
            str(event.get("resume_title", "") if isinstance(event, dict) else event["resume_title"] or ""),
            str(event.get("raw_text", "") if isinstance(event, dict) else event["raw_text"] or ""),
            vacancy_text_for_company(event.get("company_name") if isinstance(event, dict) else event["company_name"]),
        ]
    )
    event_tokens = tokens(source_text)
    matches = []
    for cv_type in load_cv_types():
        type_tokens = tokens(cv_type["content"])
        overlap = event_tokens & type_tokens
        score = 0 if not event_tokens else round(len(overlap) / max(12, min(len(event_tokens), 120)), 3)
        top_terms = [term for term, _ in Counter(overlap).most_common(12)]
        matches.append(
            {
                "slug": cv_type["slug"],
                "title": cv_type["title"],
                "score": score,
                "overlap_terms": top_terms,
                "recommendations": TYPE_RECOMMENDATIONS.get(cv_type["slug"], []),
            }
        )
    return sorted(matches, key=lambda item: item["score"], reverse=True)


def match_hh_resumes(event: sqlite3.Row | dict[str, Any]) -> list[dict[str, Any]]:
    resume_title = str(event.get("resume_title", "") if isinstance(event, dict) else event["resume_title"] or "")
    source_text = "\n".join(
        [
            resume_title,
            str(event.get("subject", "") if isinstance(event, dict) else event["subject"] or ""),
            str(event.get("raw_text", "") if isinstance(event, dict) else event["raw_text"] or ""),
        ]
    )
    event_tokens = tokens(source_text)
    matches = []
    for resume in load_hh_resumes():
        title_match = bool(resume_title and resume_title.lower() in resume["title"].lower())
        resume_tokens = tokens(resume["content"])
        overlap = event_tokens & resume_tokens
        score = 0.95 if title_match else (0 if not event_tokens else round(len(overlap) / max(8, min(len(event_tokens), 80)), 3))
        matches.append(
            {
                "id": resume["id"],
                "title": resume["title"],
                "status": resume["status"],
                "url": resume["url"],
                "score": score,
                "overlap_terms": sorted(overlap)[:12],
                "notes": resume["notes"],
            }
        )
    return sorted(matches, key=lambda item: item["score"], reverse=True)


def row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    event = dict(row)
    event["cv_type_matches"] = match_cv_types(row)
    event["hh_resume_matches"] = match_hh_resumes(row)
    event["best_match"] = (
        event["cv_type_matches"][0]
        if event["cv_type_matches"] and event["cv_type_matches"][0]["score"] > 0
        else None
    )
    event["best_hh_resume_match"] = (
        event["hh_resume_matches"][0]
        if event["hh_resume_matches"] and event["hh_resume_matches"][0]["score"] > 0
        else None
    )
    return event


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/cv-types")
def cv_types() -> list[dict[str, Any]]:
    return [
        {
            "slug": item["slug"],
            "title": item["title"],
            "keywords": sorted(tokens(item["content"]))[:80],
        }
        for item in load_cv_types()
    ]


@app.get("/api/cv-types/{slug}")
def cv_type_detail(slug: str) -> dict[str, Any]:
    detail = load_cv_type_detail(slug)
    if detail is None:
        raise HTTPException(status_code=404, detail="CV-тип не найден")
    return detail


@app.get("/api/hh-resumes")
def hh_resumes() -> list[dict[str, Any]]:
    return [
        {
            "id": item["id"],
            "title": item["title"],
            "status": item["status"],
            "channel": item["channel"],
            "external_id": item["external_id"],
            "url": item["url"],
            "notes": item["notes"],
            "source_filename": item["source_filename"],
            "updated_at": item["updated_at"],
            "keywords": resume_keywords(find_hh_resume_by_id(item["id"]) or {}, content=item["content"], limit=40),
        }
        for item in load_hh_resumes()
    ]


@app.get("/api/hh-resumes/{resume_id}")
def hh_resume_detail(resume_id: str) -> dict[str, Any]:
    item = find_hh_resume_by_id(resume_id)
    if item is None:
        raise HTTPException(status_code=404, detail="HH-резюме не найдено")
    return hh_resume_detail_payload(item)


@app.post("/api/hh-resumes/import")
async def import_hh_resume(
    file: UploadFile = File(...),
    channel: str = Form("hh"),
    title: str = Form(""),
    url: str = Form(""),
    import_mode: str = Form("new"),
    target_resume_id: str = Form(""),
) -> dict[str, Any]:
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Пустой файл резюме")

    filename = file.filename or "resume.txt"
    text = decode_resume_upload(filename, payload)
    if len(text) < 40:
        raise HTTPException(status_code=422, detail="Не удалось извлечь достаточно текста из файла резюме")

    detected_title = title.strip() or first_content_line(text) or Path(filename).stem
    now = datetime.now(timezone.utc).isoformat()
    external_id = extract_hh_resume_external_id(url, text, filename)
    existing = find_hh_resume_by_id(target_resume_id) if import_mode == "update" and target_resume_id else None
    if import_mode == "update" and not existing:
        raise HTTPException(status_code=400, detail="Для обновления выберите существующее резюме")
    if not existing and external_id:
        existing = find_hh_resume_by_external_id(external_id)
    updated_existing = existing is not None
    if existing:
        resume_id = str(existing.get("id", ""))
        created_at = str(existing.get("created_at", existing.get("imported_at", now)))
        import_count = int(existing.get("import_count", 0)) + 1
    elif external_id:
        resume_id = f"{channel}-resume-{external_id}"
        created_at = now
        import_count = 1
    else:
        digest = hashlib.sha256(f"{channel}:{detected_title}:{filename}".encode("utf-8")).hexdigest()[:16]
        resume_id = f"{channel}-manual-{digest}"
        created_at = now
        import_count = 1

    item = save_hh_resume(
        {
            "id": resume_id,
            "title": detected_title,
            "status": "current_hh" if channel == "hh" else "imported",
            "channel": channel,
            "external_id": external_id or (str(existing.get("external_id", "")) if existing else ""),
            "url": url.strip(),
            "keywords": " ".join(sorted(tokens(text))[:120]),
            "notes": f"{'Обновлено' if updated_existing else 'Импортировано'} из файла {filename} {now}",
            "source_filename": filename,
            "created_at": created_at,
            "imported_at": now,
            "updated_at": now,
            "import_count": import_count,
            "raw_text": text,
            "parsed_structure": parse_resume_structure(text, detected_title),
        }
    )
    content = "\n".join(str(item.get(key, "")) for key in ["title", "keywords", "raw_text"])
    return {
        "id": item["id"],
        "title": item["title"],
        "status": item["status"],
        "channel": item["channel"],
        "external_id": item["external_id"],
        "url": item["url"],
        "notes": item["notes"],
        "source_filename": item["source_filename"],
        "updated_at": item["updated_at"],
        "updated_existing": updated_existing,
        "keywords": resume_keywords(item, item.get("parsed_structure"), content, 40),
    }


@app.post("/api/import")
async def import_email(file: UploadFile = File(...)) -> dict[str, Any]:
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Пустой файл")
    parsed = parse_upload(file.filename or "email.txt", payload)
    if is_subject_only_mail_hint(parsed):
        raise HTTPException(
            status_code=422,
            detail="Mail передал только тему письма без тела. Используйте Electron-режим и импорт выбранного письма из Apple Mail.",
        )
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO email_events (
                created_at, source, event_type, subject, sender, sent_at, company_name,
                resume_title, confidence, raw_text, raw_filename
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                parsed["source"],
                parsed["event_type"],
                parsed["subject"],
                parsed["sender"],
                parsed["sent_at"],
                parsed["company_name"],
                parsed["resume_title"],
                parsed["confidence"],
                parsed["raw_text"],
                parsed["raw_filename"],
            ),
        )
        event_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM email_events WHERE id = ?", (event_id,)).fetchone()
    return row_to_event(row)


@app.post("/api/import/native-mail")
def import_native_mail(message: NativeMailInput) -> dict[str, Any]:
    raw_body = normalize_text(message.body)
    if looks_like_rfc822(raw_body):
        decoded = decode_upload(message.raw_filename, raw_body.encode("utf-8", errors="replace"))
        subject = decoded["subject"] or message.subject.strip() or first_subject_line(decoded["body"])
        sender = decoded["sender"] or message.sender.strip()
        sent_at = decoded["sent_at"] or message.sent_at
        body = normalize_text(decoded["body"])
    else:
        subject = message.subject.strip() or first_subject_line(raw_body)
        sender = message.sender.strip()
        sent_at = message.sent_at
        body = raw_body

    if not body:
        raise HTTPException(status_code=400, detail="Пустое письмо")

    source = detect_source(subject, sender, body)
    event_type = detect_event_type(subject, body)
    company, company_confidence = extract_company(subject, body)
    resume_title, resume_confidence = extract_resume_title(subject, body)
    confidence = max(0.1, round((company_confidence + resume_confidence) / 2, 2))

    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO email_events (
                created_at, source, event_type, subject, sender, sent_at, company_name,
                resume_title, confidence, raw_text, raw_filename
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                source,
                event_type,
                subject,
                sender,
                sent_at,
                company,
                resume_title,
                confidence,
                body,
                message.raw_filename,
            ),
        )
        event_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM email_events WHERE id = ?", (event_id,)).fetchone()
    return row_to_event(row)


@app.get("/api/events")
def events() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM email_events ORDER BY id DESC LIMIT 200").fetchall()
    return [row_to_event(row) for row in rows]


@app.get("/api/events/{event_id}")
def event(event_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM email_events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Событие не найдено")
    return row_to_event(row)


@app.post("/api/vacancies")
def add_vacancy(vacancy: VacancyInput) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO company_vacancies (created_at, company_name, title, url, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            (now, vacancy.company, vacancy.title, vacancy.url, vacancy.description),
        )
        vacancy_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM company_vacancies WHERE id = ?", (vacancy_id,)).fetchone()
    return dict(row)


@app.get("/api/vacancies")
def vacancies(company: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if company:
            rows = conn.execute(
                "SELECT * FROM company_vacancies WHERE lower(company_name) = lower(?) ORDER BY id DESC",
                (company,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM company_vacancies ORDER BY id DESC LIMIT 200").fetchall()
    return [dict(row) for row in rows]
