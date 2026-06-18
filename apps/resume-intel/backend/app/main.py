from __future__ import annotations

import html
import hashlib
import json
import os
import re
import sqlite3
import secrets
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel


def load_local_env() -> None:
    candidates = [Path.cwd() / ".env"]
    candidates.extend(parent / ".env" for parent in Path(__file__).resolve().parents)
    for env_path in candidates:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        return


load_local_env()

DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "/app/data/resume_intel.sqlite3"))
CV_TYPES_DIR = Path(os.getenv("CV_TYPES_DIR", "/workspace/output/cv_types"))
HH_RESUMES_PATH = Path(os.getenv("HH_RESUMES_PATH", "/app/config/hh_resumes.json"))
LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
HH_AUTH_URL = "https://hh.ru/oauth/authorize"
HH_TOKEN_URL = "https://hh.ru/oauth/token"
HH_ME_URL = "https://api.hh.ru/me"
HH_USER_AGENT = "ResumeIntel/0.1 (alx.matveev@yandex.ru)"

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


class HhVacancySaveInput(BaseModel):
    vacancy_id: str


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


class ResumeContentInput(BaseModel):
    content: str


class CvDocumentInput(BaseModel):
    content: str


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


def getenv_any(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
                description TEXT NOT NULL,
                source TEXT DEFAULT 'manual',
                external_id TEXT,
                employer_id TEXT,
                employer_name TEXT,
                area_name TEXT,
                salary TEXT,
                published_at TEXT,
                raw_json TEXT
            )
            """
        )
        existing_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(company_vacancies)").fetchall()
        }
        vacancy_columns = {
            "source": "TEXT DEFAULT 'manual'",
            "external_id": "TEXT",
            "employer_id": "TEXT",
            "employer_name": "TEXT",
            "area_name": "TEXT",
            "salary": "TEXT",
            "published_at": "TEXT",
            "raw_json": "TEXT",
            "import_event_id": "INTEGER",
            "recommended_resume_title": "TEXT",
            "recommended_hh_resume_id": "TEXT",
            "recommended_cv_type_slug": "TEXT",
        }
        for column, definition in vacancy_columns.items():
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE company_vacancies ADD COLUMN {column} {definition}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_accounts (
                channel TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                profile_id TEXT,
                name TEXT,
                email TEXT,
                picture_url TEXT,
                profile_url TEXT,
                raw_profile TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_states (
                state TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_tokens (
                channel TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                expires_at TEXT,
                raw_token TEXT NOT NULL
            )
            """
        )


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    backfill_vacancy_recommendations()


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
        match = re.match(r"^\s*[-*•]\s+(.+)$", line)
        if match:
            bullets.append(normalize_entity(match.group(1)))
    return bullets


def join_wrapped_lines(lines: list[str]) -> list[str]:
    joined: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if not joined:
            joined.append(stripped)
            continue

        starts_new_item = bool(
            re.match(r"^\s*[-*•]\s+", stripped)
            or re.match(r"^#{1,4}\s+", stripped)
            or looks_like_period(stripped)
        )
        previous = joined[-1]
        previous_is_open = not re.search(r"[.!?;:]$", previous)
        current_is_continuation = stripped[:1].islower() or bool(re.match(r"^[&/,)]", stripped))
        if not starts_new_item and (previous_is_open or current_is_continuation):
            joined[-1] = normalize_text(f"{previous} {stripped}")
        else:
            joined.append(stripped)
    return joined


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
        "summary": ["о себе", "обо мне", "профессиональный профиль", "ключевые кейсы и достижения", "summary", "about"],
        "skills": ["ключевые навыки", "ключевые компетенции", "навыки", "skills", "компетенции"],
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
        normalized = re.sub(r"^#{1,4}\s+", "", line).lower().strip(" .:")
        for heading, key in line_to_key.items():
            if normalized == heading or normalized.startswith(f"{heading} "):
                current = key
                sections.setdefault(current, [])
                break
        else:
            sections.setdefault(current, []).append(line)
            continue

    return {key: normalize_text("\n".join(lines)) for key, lines in sections.items() if lines}


def clean_linkedin_pdf_text(text: str) -> str:
    text = re.sub(r"(?m)^\s*Page\s+\d+\s+of\s+\d+\s*$", "", text)
    text = re.sub(r"(?m)^ \s*$", "", text)
    text = re.sub(r"([A-Za-zА-Яа-яЁё])- *\n([A-Za-zА-Яа-яЁё])", r"\1-\2", text)
    return normalize_text(text)


def split_linkedin_resume_sections(text: str) -> dict[str, str]:
    aliases = {
        "contact": ["способы связаться", "contact"],
        "skills": ["основные навыки", "top skills"],
        "languages": ["languages", "языки"],
        "certifications": ["certifications", "лицензии и сертификаты", "сертификаты"],
        "summary": ["общие сведения", "about"],
        "experience": ["опыт работы", "experience"],
        "education": ["образование", "education"],
    }
    line_to_key: dict[str, str] = {}
    for key, names in aliases.items():
        for name in names:
            line_to_key[name] = key

    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in compact_lines(clean_linkedin_pdf_text(text)):
        normalized = line.lower().strip(" .:")
        key = line_to_key.get(normalized)
        if key:
            current = key
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)

    return {key: normalize_text("\n".join(lines)) for key, lines in sections.items() if lines}


def extract_linkedin_identity(text: str) -> dict[str, str]:
    lines = compact_lines(clean_linkedin_pdf_text(text))
    summary_idx = next((idx for idx, line in enumerate(lines) if line.lower().strip(" .:") in {"общие сведения", "about"}), -1)
    if summary_idx < 0:
        return {"name": "", "headline": "", "location": ""}

    start_idx = max(0, summary_idx - 10)
    candidates = lines[start_idx:summary_idx]
    name_idx = -1
    for idx, line in enumerate(candidates):
        if re.fullmatch(r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё.-]+ [A-ZА-ЯЁ][A-Za-zА-Яа-яЁё.-]+(?: [A-ZА-ЯЁ][A-Za-zА-Яа-яЁё.-]+)?", line):
            name_idx = idx
            break
    if name_idx < 0:
        return {"name": "", "headline": "", "location": ""}

    name = normalize_entity(candidates[name_idx])
    after_name = candidates[name_idx + 1:]
    location = after_name[-1] if after_name else ""
    headline_lines = after_name[:-1] if len(after_name) > 1 else after_name
    return {
        "name": name,
        "headline": normalize_text(" ".join(headline_lines)),
        "location": normalize_entity(location),
    }


def split_skills(value: str) -> list[str]:
    skills = []
    for part in re.split(r"[,;•\n]+", value):
        cleaned = normalize_entity(part)
        if 2 < len(cleaned) <= 80:
            skills.append(cleaned)
    return list(dict.fromkeys(skills))


def strip_markdown_inline(value: str) -> str:
    value = re.sub(r"^#{1,4}\s+", "", value.strip())
    value = re.sub(r"^\*\*(.+?)\*\*$", r"\1", value)
    return normalize_entity(value)


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
            raw_period = strip_markdown_inline(lines[period_idx])
            markdown_period_match = re.match(r"(.+?[—-].+?)[—-]\s+(.+)$", raw_period)
            for idx in range(period_idx - 1, company_start - 1, -1):
                line = lines[idx]
                if line.startswith("-"):
                    break
                if looks_like_duration(line):
                    continue
                company_candidates.append(line)
            company_candidates = list(reversed(company_candidates))

            if markdown_period_match:
                period_parts = [normalize_entity(markdown_period_match.group(1))]
                company_candidates = [normalize_entity(markdown_period_match.group(2))]
            else:
                period_parts = [raw_period]
            cursor = period_idx + 1
            if cursor < len(lines) and re.search(r"(19|20)\d{2}|настоящее|present", lines[cursor], re.I):
                period_parts.append(lines[cursor])
                cursor += 1
            if cursor < len(lines) and looks_like_duration(lines[cursor]):
                cursor += 1

            position = strip_markdown_inline(lines[cursor]) if cursor < len(lines) else ""
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


def looks_like_location(line: str) -> bool:
    lowered = line.lower()
    return (
        "," in line
        and any(value in lowered for value in ["россия", "russia", "москва", "moscow", "санкт-петербург"])
        and len(line) <= 80
    )


def linkedin_experience_header_start(lines: list[str], period_idx: int) -> int:
    cursor = period_idx - 1
    header_start = cursor
    while cursor >= 0 and period_idx - cursor <= 4:
        line = lines[cursor].strip()
        if not line:
            break
        if line.startswith(("-", "•", "*")):
            break
        if cursor < period_idx - 1 and re.search(r"[.!?]$", line):
            break
        header_start = cursor
        cursor -= 1
    return header_start


def parse_linkedin_experience_entries(experience_text: str) -> list[dict[str, Any]]:
    lines = compact_lines(experience_text)
    period_indexes = [idx for idx, line in enumerate(lines) if looks_like_period(line)]
    if not period_indexes:
        return parse_experience_entries(experience_text)

    header_starts = [linkedin_experience_header_start(lines, idx) for idx in period_indexes]
    entries: list[dict[str, Any]] = []
    for order, period_idx in enumerate(period_indexes):
        header_start = header_starts[order]
        header_lines = lines[header_start:period_idx]
        company = normalize_entity(header_lines[0]) if header_lines else ""
        position = normalize_entity(" ".join(header_lines[1:])) if len(header_lines) > 1 else ""

        body_start = period_idx + 1
        if body_start < len(lines) and looks_like_location(lines[body_start]):
            body_start += 1
        body_end = header_starts[order + 1] if order + 1 < len(header_starts) else len(lines)
        description = normalize_text("\n".join(join_wrapped_lines(lines[body_start:body_end])))

        entries.append(
            {
                "period": normalize_entity(lines[period_idx]),
                "company": company,
                "position": position,
                "description": description,
                "achievements": extract_bullets(description),
            }
        )
    return entries


def parse_linkedin_resume_structure(text: str, title: str) -> dict[str, Any]:
    cleaned_text = clean_linkedin_pdf_text(text)
    sections = split_linkedin_resume_sections(cleaned_text)
    identity = extract_linkedin_identity(cleaned_text)
    profile_title = title
    if identity.get("name"):
        profile_title = identity["name"]
        if identity.get("headline"):
            profile_title = f"{profile_title} — {identity['headline']}"

    header_lines = [
        value
        for value in [
            identity.get("name", ""),
            identity.get("headline", ""),
            identity.get("location", ""),
        ]
        if value
    ]
    header_lines.extend(compact_lines(sections.get("contact", ""))[:6])

    return {
        "title": profile_title,
        "header": header_lines[:12],
        "summary": sections.get("summary", ""),
        "skills": split_skills(sections.get("skills", "")),
        "experience": parse_linkedin_experience_entries(sections.get("experience", "")),
        "education": compact_lines(sections.get("education", "")),
        "certifications": compact_lines(sections.get("certifications", "")),
        "languages": compact_lines(sections.get("languages", "")),
        "sections": [
            {"key": key, "title": key, "content": value}
            for key, value in sections.items()
            if key != "header" and value
        ],
        "parser_notes": [
            "LinkedIn PDF разобран по типовым заголовкам профиля; исходный текст сохранен полностью в raw_text.",
        ],
    }


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


def parse_resume_for_channel(channel: str, text: str, title: str) -> dict[str, Any]:
    if channel == "linkedin":
        return parse_linkedin_resume_structure(text, title)
    return parse_resume_structure(text, title)


def detect_source(subject: str, sender: str, text: str) -> str:
    value = f"{subject}\n{sender}\n{text[:1000]}".lower()
    if "hh.ru" in value or "headhunter" in value or "хэдхантер" in value:
        return "hh"
    if "facancy" in value:
        return "facancy"
    if "linkedin" in value:
        return "linkedin"
    return "unknown"


def detect_event_type(subject: str, text: str) -> str:
    value = f"{subject}\n{text[:1500]}".lower()
    if "привлекло внимание" in value or "просмотр" in value or "посмотрел" in value:
        return "resume_attention"
    if "подходящие вакансии" in value or "recommended jobs" in value or "вот лучшие для вас вакансии" in value or "вот хорошие для вас вакансии" in value:
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


def decode_native_mail_message(message: NativeMailInput) -> dict[str, str]:
    raw_body = normalize_text(message.body)
    if looks_like_rfc822(raw_body):
        decoded = decode_upload(message.raw_filename, raw_body.encode("utf-8", errors="replace"))
        return {
            "subject": decoded["subject"] or message.subject.strip() or first_subject_line(decoded["body"]),
            "sender": decoded["sender"] or message.sender.strip(),
            "sent_at": decoded["sent_at"] or message.sent_at,
            "body": normalize_text(decoded["body"]),
            "raw_filename": message.raw_filename,
        }
    return {
        "subject": message.subject.strip() or first_subject_line(raw_body),
        "sender": message.sender.strip(),
        "sent_at": message.sent_at,
        "body": raw_body,
        "raw_filename": message.raw_filename,
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
        documents = [path for path in [skills_path, analysis_path, cv_path] if path.exists()]
        updated_at = max((path.stat().st_mtime for path in documents), default=folder.stat().st_mtime)
        items.append({"slug": folder.name, "title": title, "content": content, "updated_at": updated_at})
    return sorted(items, key=lambda item: float(item.get("updated_at", 0)), reverse=True)


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


ALLOWED_CV_DOCUMENTS = {
    "analysis.md",
    "skills_requirements.md",
    "tailored_cv.md",
    "cover_letter_template.md",
    "interview_prep.md",
    "source_cases.md",
}


def save_cv_type_document(slug: str, filename: str, content: str) -> dict[str, Any]:
    if filename not in ALLOWED_CV_DOCUMENTS:
        raise HTTPException(status_code=400, detail="Этот документ CV-типа нельзя редактировать из UI")
    folder = CV_TYPES_DIR / slug
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail="CV-тип не найден")
    path = folder / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Документ CV-типа не найден")
    try:
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
    except OSError as error:
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось сохранить документ CV-типа: {error}",
        ) from error
    detail = load_cv_type_detail(slug)
    if detail is None:
        raise HTTPException(status_code=404, detail="CV-тип не найден после сохранения")
    return detail


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
                "source": str(item.get("source", "manual")),
                "external_id": str(item.get("external_id", "")),
                "url": str(item.get("url", "")),
                "notes": str(item.get("notes", "")),
                "source_filename": str(item.get("source_filename", "")),
                "updated_at": str(item.get("updated_at", item.get("imported_at", ""))),
                "api_updated_at": str(item.get("api_updated_at", "")),
                "content": content,
            }
        )
    return sorted(result, key=lambda item: item.get("updated_at", ""), reverse=True)


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
    parsed_structure = item.get("parsed_structure") or parse_resume_for_channel(
        str(item.get("channel", "hh")),
        str(item.get("raw_text", "")),
        str(item.get("title", "")),
    )
    return {
        "id": str(item.get("id", "")),
        "title": str(item.get("title", "")),
        "status": str(item.get("status", "current_hh")),
        "channel": str(item.get("channel", "hh")),
        "source": str(item.get("source", "manual")),
        "external_id": str(item.get("external_id", "")),
        "url": str(item.get("url", "")),
        "notes": str(item.get("notes", "")),
        "source_filename": str(item.get("source_filename", "")),
        "created_at": str(item.get("created_at", "")),
        "updated_at": str(item.get("updated_at", item.get("imported_at", ""))),
        "api_imported_at": str(item.get("api_imported_at", "")),
        "api_updated_at": str(item.get("api_updated_at", "")),
        "import_count": int(item.get("import_count", 0) or 0),
        "raw_text": str(item.get("raw_text", "")),
        "raw_api_data": item.get("raw_api_data"),
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


def hh_api_get(path: str, *, access_token: str | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    token = access_token or hh_access_token()
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    return request_json(f"https://api.hh.ru{path}{query}", access_token=token, provider="HH")


def hh_api_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("id") or "").strip()
    if isinstance(value, list):
        return ", ".join(item for item in (hh_api_name(part) for part in value) if item)
    return str(value or "").strip()


def hh_api_period(start: str, end: str | None = None) -> str:
    if start and end:
        return f"{start} — {end}"
    if start:
        return f"{start} — настоящее время"
    return ""


def parse_hh_api_resume_structure(resume: dict[str, Any]) -> dict[str, Any]:
    title = str(resume.get("title") or resume.get("id") or "HH resume")
    first_name = str(resume.get("first_name", "") or "").strip()
    last_name = str(resume.get("last_name", "") or "").strip()
    middle_name = str(resume.get("middle_name", "") or "").strip()
    full_name = " ".join(part for part in [last_name, first_name, middle_name] if part)
    header = [
        value
        for value in [
            full_name,
            title,
            hh_api_name(resume.get("area")),
            str(resume.get("age", "") or ""),
            hh_api_name(resume.get("employment")),
            hh_api_name(resume.get("schedule")),
        ]
        if value
    ]

    skill_set = [str(skill).strip() for skill in resume.get("skill_set", []) if str(skill).strip()]
    skills_text = str(resume.get("skills", "") or "").strip()
    if skills_text and skills_text not in skill_set:
        skill_set.append(skills_text)

    experience = []
    for item in resume.get("experience", []) or []:
        if not isinstance(item, dict):
            continue
        period = hh_api_period(str(item.get("start", "") or ""), item.get("end"))
        description = normalize_text(str(item.get("description", "") or ""))
        experience.append(
            {
                "period": period,
                "company": str(item.get("company", "") or ""),
                "position": str(item.get("position", "") or ""),
                "description": description,
                "achievements": extract_bullets(description),
            }
        )

    education_lines = []
    education = resume.get("education") or {}
    if isinstance(education, dict):
        for education_item in education.get("primary", []) or []:
            if not isinstance(education_item, dict):
                continue
            education_lines.append(
                normalize_entity(
                    ", ".join(
                        part
                        for part in [
                            str(education_item.get("name", "") or ""),
                            str(education_item.get("organization", "") or ""),
                            str(education_item.get("result", "") or ""),
                            str(education_item.get("year", "") or ""),
                        ]
                        if part
                    )
                )
            )

    languages = []
    for language in resume.get("language", []) or []:
        if not isinstance(language, dict):
            continue
        languages.append(
            " — ".join(part for part in [hh_api_name(language), hh_api_name(language.get("level"))] if part)
        )

    certifications = []
    for certificate in resume.get("certificate", []) or []:
        if not isinstance(certificate, dict):
            continue
        certifications.append(
            normalize_entity(
                ", ".join(
                    part
                    for part in [
                        str(certificate.get("title", "") or ""),
                        str(certificate.get("company", "") or ""),
                        str(certificate.get("achieved_at", "") or ""),
                    ]
                    if part
                )
            )
        )

    sections = [
        {"key": key, "title": key, "content": normalize_text(json.dumps(value, ensure_ascii=False))}
        for key, value in resume.items()
        if value not in ("", None, [], {})
    ]
    return {
        "title": title,
        "header": header[:12],
        "summary": skills_text,
        "skills": skill_set,
        "experience": experience,
        "education": [line for line in education_lines if line],
        "certifications": [line for line in certifications if line],
        "languages": [line for line in languages if line],
        "sections": sections,
        "parser_notes": [
            "HH API resume сохранено как структурированный JSON; raw_api_data содержит полный ответ HH без потери дополнительных секций.",
        ],
    }


def render_hh_api_resume_text(resume: dict[str, Any], structure: dict[str, Any]) -> str:
    lines = [
        f"Резюме \"{structure['title']}\"",
        f"HH resume id: {resume.get('id', '')}",
        f"Обновлено в HH: {resume.get('updated_at', '')}",
        "",
    ]
    if structure.get("header"):
        lines.extend(str(line) for line in structure["header"])
        lines.append("")
    if structure.get("summary"):
        lines.extend(["Обо мне", str(structure["summary"]), ""])
    if structure.get("skills"):
        lines.extend(["Навыки", "; ".join(structure["skills"]), ""])
    if structure.get("experience"):
        lines.append("Опыт работы")
        for item in structure["experience"]:
            lines.extend(
                [
                    str(item.get("company", "")),
                    str(item.get("position", "")),
                    str(item.get("period", "")),
                    str(item.get("description", "")),
                    "",
                ]
            )
    if structure.get("education"):
        lines.extend(["Образование", *structure["education"], ""])
    if structure.get("certifications"):
        lines.extend(["Сертификации", *structure["certifications"], ""])
    if structure.get("languages"):
        lines.extend(["Языки", *structure["languages"], ""])
    return normalize_text("\n".join(lines))


def save_hh_api_resume(resume: dict[str, Any]) -> dict[str, Any]:
    external_id = str(resume.get("id", "") or "")
    if not external_id:
        raise HTTPException(status_code=502, detail="HH API вернул резюме без id")
    structure = parse_hh_api_resume_structure(resume)
    raw_text = render_hh_api_resume_text(resume, structure)
    now = utc_now()
    existing = find_hh_resume_by_external_id(external_id)
    created_at = str((existing or {}).get("created_at", now))
    import_count = int((existing or {}).get("import_count", 0) or 0) + 1
    item = {
        **(existing or {}),
        "id": str((existing or {}).get("id") or f"hh-resume-{external_id}"),
        "title": structure["title"],
        "status": "current_hh",
        "channel": "hh",
        "source": "hh_api",
        "external_id": external_id,
        "url": str(resume.get("alternate_url", "") or (existing or {}).get("url", "")),
        "keywords": " ".join(resume_keywords({}, structure, raw_text, 160)),
        "notes": f"Синхронизировано из HH API {now}",
        "source_filename": "",
        "created_at": created_at,
        "imported_at": now,
        "updated_at": now,
        "api_imported_at": now,
        "api_updated_at": str(resume.get("updated_at", "") or ""),
        "import_count": import_count,
        "raw_text": raw_text,
        "parsed_structure": structure,
        "raw_api_data": resume,
    }
    return save_hh_resume(item)


def hh_salary_text(salary: Any) -> str:
    if not isinstance(salary, dict) or not salary:
        return ""
    parts = []
    if salary.get("from") is not None:
        parts.append(f"от {salary['from']}")
    if salary.get("to") is not None:
        parts.append(f"до {salary['to']}")
    if salary.get("currency"):
        parts.append(str(salary["currency"]))
    if salary.get("gross") is not None:
        parts.append("до вычета налогов" if salary.get("gross") else "на руки")
    return " ".join(parts)


def hh_vacancy_description(vacancy: dict[str, Any]) -> str:
    parts = [
        strip_html(str(vacancy.get("description", "") or "")),
        strip_html(str((vacancy.get("snippet") or {}).get("requirement", "") or "")),
        strip_html(str((vacancy.get("snippet") or {}).get("responsibility", "") or "")),
    ]
    return normalize_text("\n\n".join(part for part in parts if part.strip()))


def hh_vacancy_payload(vacancy: dict[str, Any]) -> dict[str, Any]:
    employer = vacancy.get("employer") if isinstance(vacancy.get("employer"), dict) else {}
    area = vacancy.get("area") if isinstance(vacancy.get("area"), dict) else {}
    return {
        "source": "hh_api",
        "external_id": str(vacancy.get("id", "") or ""),
        "company_name": str(employer.get("name") or ""),
        "employer_id": str(employer.get("id") or ""),
        "employer_name": str(employer.get("name") or ""),
        "title": str(vacancy.get("name", "") or ""),
        "url": str(vacancy.get("alternate_url", "") or vacancy.get("url", "") or ""),
        "description": hh_vacancy_description(vacancy),
        "area_name": str(area.get("name") or ""),
        "salary": hh_salary_text(vacancy.get("salary")),
        "published_at": str(vacancy.get("published_at", "") or ""),
        "raw_json": json.dumps(vacancy, ensure_ascii=False),
    }


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def resolve_recommended_profile(resume_title: str) -> dict[str, str]:
    normalized_title = normalize_entity(resume_title)
    if not normalized_title:
        return {
            "recommended_resume_title": "",
            "recommended_hh_resume_id": "",
            "recommended_cv_type_slug": "",
        }

    best_resume: dict[str, str] | None = None
    best_resume_score = 0.0
    title_tokens = tokens(normalized_title)
    for resume in load_hh_resumes():
        resume_title_lower = resume["title"].lower()
        digest_title_lower = normalized_title.lower()
        title_match = (
            digest_title_lower in resume_title_lower
            or resume_title_lower in digest_title_lower
            or normalized_title.lower() == resume.get("notes", "").lower()
        )
        overlap = title_tokens & tokens(resume["content"])
        score = 0.95 if title_match else round(len(overlap) / max(6, len(title_tokens)), 3)
        if score > best_resume_score:
            best_resume_score = score
            best_resume = resume

    best_cv: dict[str, str] | None = None
    best_cv_score = 0.0
    for cv_type in load_cv_types():
        overlap = title_tokens & tokens(cv_type["content"])
        title_overlap = title_tokens & tokens(cv_type["title"])
        score = round((len(overlap) + len(title_overlap) * 2) / max(8, len(title_tokens)), 3)
        if score > best_cv_score:
            best_cv_score = score
            best_cv = cv_type

    return {
        "recommended_resume_title": normalized_title,
        "recommended_hh_resume_id": best_resume["id"] if best_resume and best_resume_score >= 0.12 else "",
        "recommended_cv_type_slug": best_cv["slug"] if best_cv and best_cv_score >= 0.08 else "",
    }


def build_recommended_profile(row: dict[str, Any]) -> dict[str, Any] | None:
    resume_title = str(row.get("recommended_resume_title") or "")
    hh_id = str(row.get("recommended_hh_resume_id") or "")
    cv_slug = str(row.get("recommended_cv_type_slug") or "")
    import_event_id = row.get("import_event_id")
    if not resume_title and not hh_id and not cv_slug and not import_event_id:
        return None

    hh_title = ""
    if hh_id:
        for resume in load_hh_resumes():
            if resume["id"] == hh_id:
                hh_title = resume["title"]
                break

    cv_title = ""
    if cv_slug:
        for cv_type in load_cv_types():
            if cv_type["slug"] == cv_slug:
                cv_title = cv_type["title"]
                break

    return {
        "import_event_id": import_event_id,
        "resume_title": resume_title,
        "hh_resume_id": hh_id,
        "hh_resume_title": hh_title,
        "cv_type_slug": cv_slug,
        "cv_type_title": cv_title,
    }


def sources_compatible(vacancy_source: str | None, event_source: str | None) -> bool:
    vacancy_source = str(vacancy_source or "")
    event_source = str(event_source or "")
    if vacancy_source == event_source:
        return True
    if event_source == "hh" and vacancy_source == "hh_api":
        return True
    return False


def vacancy_linked_to_event(row: sqlite3.Row | dict[str, Any], event: sqlite3.Row | dict[str, Any]) -> bool:
    row_dict = dict(row)
    event_dict = dict(event)
    raw_json = str(row_dict.get("raw_json") or "")
    raw_filename = str(event_dict.get("raw_filename") or "")
    if raw_filename and raw_filename in raw_json:
        return True

    mail_subject = ""
    if raw_json:
        try:
            mail_subject = str(json.loads(raw_json).get("mail_subject") or "")
        except json.JSONDecodeError:
            mail_subject = ""
    if mail_subject and mail_subject == str(event_dict.get("subject") or ""):
        return True

    row_created = parse_iso_datetime(str(row_dict.get("created_at") or ""))
    event_created = parse_iso_datetime(str(event_dict.get("created_at") or ""))
    if row_created and event_created:
        delta = abs((row_created - event_created).total_seconds())
        return delta <= 30 and sources_compatible(str(row_dict.get("source") or ""), str(event_dict.get("source") or ""))
    return False


def backfill_vacancy_recommendations() -> dict[str, int]:
    updated = 0
    cleared = 0
    with connect() as conn:
        events = conn.execute(
            "SELECT * FROM email_events WHERE event_type = 'recommended_jobs' ORDER BY id DESC"
        ).fetchall()
        vacancies = conn.execute("SELECT * FROM company_vacancies ORDER BY id").fetchall()
        events_by_id = {event["id"]: event for event in events}

        for vacancy in vacancies:
            vacancy_dict = dict(vacancy)
            event_id = vacancy_dict.get("import_event_id")
            if event_id:
                event = events_by_id.get(event_id)
                if not event or not vacancy_linked_to_event(vacancy, event):
                    conn.execute(
                        """
                        UPDATE company_vacancies
                        SET import_event_id = NULL,
                            recommended_resume_title = '',
                            recommended_hh_resume_id = '',
                            recommended_cv_type_slug = ''
                        WHERE id = ?
                        """,
                        (vacancy["id"],),
                    )
                    cleared += 1
                    vacancy_dict = {
                        **vacancy_dict,
                        "import_event_id": None,
                        "recommended_resume_title": "",
                        "recommended_hh_resume_id": "",
                        "recommended_cv_type_slug": "",
                    }

            if vacancy_dict.get("recommended_resume_title"):
                continue

            linked_event = None
            for event in events:
                if not vacancy_linked_to_event(vacancy, event):
                    continue
                resume_title = str(event["resume_title"] or "")
                if not resume_title:
                    resume_title = extract_digest_resume_title(str(event["subject"] or ""), str(event["raw_text"] or ""))
                if resume_title:
                    linked_event = event
                    break

            if not linked_event:
                continue

            resume_title = str(linked_event["resume_title"] or "")
            if not resume_title:
                resume_title = extract_digest_resume_title(
                    str(linked_event["subject"] or ""),
                    str(linked_event["raw_text"] or ""),
                )
            profile = resolve_recommended_profile(resume_title)
            if not profile["recommended_resume_title"]:
                continue

            conn.execute(
                """
                UPDATE company_vacancies
                SET import_event_id = ?, recommended_resume_title = ?,
                    recommended_hh_resume_id = ?, recommended_cv_type_slug = ?
                WHERE id = ?
                """,
                (
                    linked_event["id"],
                    profile["recommended_resume_title"],
                    profile["recommended_hh_resume_id"],
                    profile["recommended_cv_type_slug"],
                    vacancy["id"],
                ),
            )
            updated += 1
    return {"updated": updated, "cleared": cleared}


def vacancy_match_payload(vacancy: dict[str, Any]) -> dict[str, Any]:
    source_text = "\n".join(
        str(vacancy.get(key, "") or "")
        for key in ["title", "company_name", "employer_name", "description"]
    )
    vacancy_tokens = tokens(source_text)
    cv_matches = []
    for cv_type in load_cv_types():
        overlap = vacancy_tokens & tokens(cv_type["content"])
        score = 0 if not vacancy_tokens else round(len(overlap) / max(12, min(len(vacancy_tokens), 160)), 3)
        cv_matches.append(
            {
                "slug": cv_type["slug"],
                "title": cv_type["title"],
                "score": score,
                "overlap_terms": sorted(overlap)[:12],
                "recommendations": TYPE_RECOMMENDATIONS.get(cv_type["slug"], []),
            }
        )

    resume_matches = []
    for resume in load_hh_resumes():
        overlap = vacancy_tokens & tokens(resume["content"])
        score = 0 if not vacancy_tokens else round(len(overlap) / max(8, min(len(vacancy_tokens), 120)), 3)
        resume_matches.append(
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

    recommended_profile = build_recommended_profile(vacancy)
    if recommended_profile:
        for match in cv_matches:
            if match["slug"] == recommended_profile.get("cv_type_slug"):
                match["recommended"] = True
                match["score"] = max(match["score"], 0.9)
        for match in resume_matches:
            if match["id"] == recommended_profile.get("hh_resume_id"):
                match["recommended"] = True
                match["score"] = max(match["score"], 0.9)

    return {
        **vacancy,
        "recommended_profile": recommended_profile,
        "cv_type_matches": sorted(cv_matches, key=lambda item: item["score"], reverse=True),
        "hh_resume_matches": sorted(resume_matches, key=lambda item: item["score"], reverse=True),
    }


def vacancy_payload(row: dict[str, Any]) -> dict[str, Any]:
    return vacancy_match_payload(
        {
            **row,
            "raw_json": row.get("raw_json", ""),
        }
    )


def save_company_vacancy(payload: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    company = normalize_entity(str(payload.get("company_name") or payload.get("employer_name") or "Компания не определена"))
    title = normalize_entity(str(payload.get("title") or "Вакансия без названия"))
    description = normalize_text(str(payload.get("description") or "")) or title
    values = {
        "created_at": now,
        "company_name": company,
        "title": title,
        "url": str(payload.get("url") or ""),
        "description": description,
        "source": str(payload.get("source") or "manual"),
        "external_id": str(payload.get("external_id") or ""),
        "employer_id": str(payload.get("employer_id") or ""),
        "employer_name": normalize_entity(str(payload.get("employer_name") or company)),
        "area_name": normalize_entity(str(payload.get("area_name") or "")),
        "salary": str(payload.get("salary") or ""),
        "published_at": str(payload.get("published_at") or ""),
        "raw_json": str(payload.get("raw_json") or ""),
        "import_event_id": payload.get("import_event_id"),
        "recommended_resume_title": str(payload.get("recommended_resume_title") or ""),
        "recommended_hh_resume_id": str(payload.get("recommended_hh_resume_id") or ""),
        "recommended_cv_type_slug": str(payload.get("recommended_cv_type_slug") or ""),
    }
    with connect() as conn:
        existing = None
        if values["source"] == "hh_api" and values["external_id"]:
            existing = conn.execute(
                "SELECT * FROM company_vacancies WHERE source = ? AND external_id = ?",
                (values["source"], values["external_id"]),
            ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE company_vacancies
                SET created_at = ?, company_name = ?, title = ?, url = ?, description = ?,
                    employer_id = ?, employer_name = ?, area_name = ?, salary = ?,
                    published_at = ?, raw_json = ?, import_event_id = ?,
                    recommended_resume_title = ?, recommended_hh_resume_id = ?,
                    recommended_cv_type_slug = ?
                WHERE id = ?
                """,
                (
                    values["created_at"], values["company_name"], values["title"], values["url"],
                    values["description"], values["employer_id"], values["employer_name"], values["area_name"],
                    values["salary"], values["published_at"], values["raw_json"], values["import_event_id"],
                    values["recommended_resume_title"], values["recommended_hh_resume_id"],
                    values["recommended_cv_type_slug"], existing["id"],
                ),
            )
            vacancy_id = existing["id"]
        else:
            cursor = conn.execute(
                """
                INSERT INTO company_vacancies (
                    created_at, company_name, title, url, description, source,
                    external_id, employer_id, employer_name, area_name, salary,
                    published_at, raw_json, import_event_id, recommended_resume_title,
                    recommended_hh_resume_id, recommended_cv_type_slug
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["created_at"], values["company_name"], values["title"], values["url"],
                    values["description"], values["source"], values["external_id"], values["employer_id"],
                    values["employer_name"], values["area_name"], values["salary"], values["published_at"],
                    values["raw_json"], values["import_event_id"], values["recommended_resume_title"],
                    values["recommended_hh_resume_id"], values["recommended_cv_type_slug"],
                ),
            )
            vacancy_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM company_vacancies WHERE id = ?", (vacancy_id,)).fetchone()
    return vacancy_payload(dict(row))


VACANCY_DIGEST_STOP_LINES = {
    "посмотреть еще",
    "если нужна помощь",
    "написать в поддержку",
    "управлять рассылкой",
    "оставайтесь на связи",
    "мобильное приложение",
}


def looks_like_salary_line(line: str) -> bool:
    lowered = line.lower()
    return bool(
        re.search(r"\d", line)
        and any(value in lowered for value in ["₽", "руб", "€", "$", "usd", "eur", "за месяц", "gross", "net", "от ", "до "])
    )


def looks_like_footer_line(line: str) -> bool:
    lowered = line.lower().strip(" .")
    return (
        lowered in VACANCY_DIGEST_STOP_LINES
        or "вы получили это письмо" in lowered
        or "ооо" in lowered and "хэдхантер" in lowered
        or lowered.startswith("©")
    )


def extract_digest_resume_title(subject: str, body: str) -> str:
    value = f"{subject}\n{body}"
    title = find_first(
        [
            r"(?:новые|подходящие)\s+вакансии\s+для\s+резюме:\s*[«\"]?([^»\"\n]+)",
            r"recommended jobs for resume:\s*[«\"]?([^»\"\n]+)",
        ],
        value,
    )
    return normalize_entity(title or "")


def facancy_section_title(line: str) -> str:
    lowered = line.lower().strip(" .:")
    if "лучшие для вас вакансии" in lowered:
        return "лучшие для вас вакансии"
    if "хорошие для вас вакансии" in lowered:
        return "хорошие для вас вакансии"
    if "любопытно посмотреть" in lowered:
        return "любопытно посмотреть"
    if "показать вашим детям" in lowered:
        return "рекомендуем показать детям"
    return ""


def split_facancy_title_company(value: str) -> tuple[str, str]:
    cleaned = normalize_entity(value)
    patterns = [
        r"^(.+?)\s+в\s+(российскую\s+IT-компанию)$",
        r"^(.+?)\s+в\s+(IT-компанию(?:\s+.+)?)$",
        r"^(.+?)\s+в\s+(образовательную\s+компанию\s+.+)$",
        r"^(.+?)\s+в\s+(бренд\s+.+)$",
        r"^(.+?)\s+в\s+(систему\s+.+)$",
        r"^(.+?)\s+в\s+(туристическую\s+компанию\s+.+)$",
        r"^(.+?)\s+в\s+(команду\s+.+)$",
        r"^(.+?)\s+в\s+([A-ZА-ЯЁ0-9][^\n]+)$",
        r"^(.+?)\s+от\s+(.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, cleaned)
        if match:
            return normalize_entity(match.group(1)), normalize_entity(match.group(2))
    return cleaned, ""


def parse_facancy_digest(lines: list[str]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    current_section = ""
    idx = 0
    while idx < len(lines):
        line = normalize_entity(lines[idx])
        section = facancy_section_title(line)
        if section:
            current_section = section
            idx += 1
            continue
        if looks_like_footer_line(line) or "посмотреть все вакансии" in line.lower():
            break
        if line.startswith("•"):
            raw_title = normalize_entity(line.lstrip("• "))
            title, company = split_facancy_title_company(raw_title)
            tags: list[str] = []
            next_idx = idx + 1
            while next_idx < len(lines):
                next_line = normalize_entity(lines[next_idx])
                if not next_line or next_line.startswith("•") or facancy_section_title(next_line) or looks_like_footer_line(next_line):
                    break
                if len(next_line) <= 120:
                    tags.append(next_line)
                next_idx += 1
            description_parts = [
                f"Категория Facancy: {current_section}" if current_section else "",
                f"Исходная строка: {raw_title}",
                f"Метки: {'; '.join(tags)}" if tags else "",
            ]
            candidates.append(
                {
                    "title": title,
                    "company": company,
                    "salary": "",
                    "description": normalize_text("\n".join(part for part in description_parts if part)),
                }
            )
            idx = next_idx
            continue
        idx += 1
    return candidates


def parse_vacancy_digest(subject: str, sender: str, body: str) -> dict[str, Any]:
    source = detect_source(subject, sender, body)
    resume_title = extract_digest_resume_title(subject, body)
    lines = compact_lines(body)
    if source == "facancy":
        return {
            "source": source,
            "resume_title": resume_title,
            "vacancies": parse_facancy_digest(lines),
        }

    start_idx = 0
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if "вакансии для резюме" in lowered or "recommended jobs" in lowered:
            start_idx = idx + 1
            break

    candidates: list[dict[str, str]] = []
    idx = start_idx
    while idx < len(lines):
        line = normalize_entity(lines[idx])
        if not line:
            idx += 1
            continue
        if looks_like_footer_line(line):
            break
        if looks_like_salary_line(line):
            idx += 1
            continue

        title = line
        idx += 1
        salary = ""
        company = ""
        if idx < len(lines) and looks_like_salary_line(lines[idx]):
            salary = normalize_entity(lines[idx])
            idx += 1
        if idx < len(lines) and not looks_like_footer_line(lines[idx]):
            company_candidate = normalize_entity(lines[idx])
            if not looks_like_salary_line(company_candidate):
                company = company_candidate
                idx += 1
        if 3 <= len(title) <= 160:
            candidates.append({"title": title, "company": company, "salary": salary})

    return {
        "source": source,
        "resume_title": resume_title,
        "vacancies": candidates,
    }


def enrich_hh_digest_vacancy(title: str, company: str) -> dict[str, Any] | None:
    query = " ".join(part for part in [title, company] if part).strip()
    if not query:
        return None
    try:
        result = hh_public_get("/vacancies", {"text": query, "per_page": 3})
    except HTTPException:
        return None
    items = result.get("items", []) if isinstance(result, dict) else []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        item_title = str(item.get("name", "") or "").lower()
        employer = item.get("employer") if isinstance(item.get("employer"), dict) else {}
        employer_name = str(employer.get("name", "") or "").lower()
        title_ok = title.lower() in item_title or item_title in title.lower()
        company_ok = not company or company.lower() in employer_name or employer_name in company.lower()
        if title_ok and company_ok:
            vacancy_id = str(item.get("id", "") or "")
            if vacancy_id:
                return hh_public_get(f"/vacancies/{urllib.parse.quote(vacancy_id)}")
    if items and isinstance(items[0], dict) and items[0].get("id"):
        return hh_public_get(f"/vacancies/{urllib.parse.quote(str(items[0]['id']))}")
    return None


def vacancies_for_company(company_name: str | None, limit: int = 8) -> list[dict[str, Any]]:
    if not company_name:
        return []
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM company_vacancies WHERE lower(company_name) = lower(?) OR lower(employer_name) = lower(?) ORDER BY id DESC LIMIT ?",
            (company_name, company_name, limit),
        ).fetchall()
    return [vacancy_payload(dict(row)) for row in rows]


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
    company_name = event.get("company_name") if isinstance(event, dict) else event["company_name"]
    source_text = "\n".join(
        [
            resume_title,
            str(event.get("subject", "") if isinstance(event, dict) else event["subject"] or ""),
            str(event.get("raw_text", "") if isinstance(event, dict) else event["raw_text"] or ""),
            vacancy_text_for_company(company_name),
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
    event["related_vacancies"] = vacancies_for_company(event.get("company_name"))
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


def linkedin_config() -> dict[str, Any]:
    client_id = getenv_any("LINKEDIN_CLIENT_ID", "linkedin_client_id")
    client_secret = getenv_any("LINKEDIN_CLIENT_SECRET", "linkedin_client_secret")
    redirect_uri = getenv_any(
        "LINKEDIN_REDIRECT_URI",
        "linkedin_redirect_uri",
        default="http://localhost:8787/api/channels/linkedin/oauth/callback",
    )
    scopes = getenv_any("LINKEDIN_SCOPES", "linkedin_scopes", default="openid profile email")
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "scopes": scopes,
        "configured": bool(client_id and client_secret and redirect_uri),
    }


def hh_config() -> dict[str, Any]:
    client_id = getenv_any("HH_CLIENT_ID", "hh_client_id")
    client_secret = getenv_any("HH_CLIENT_SECRET", "hh_client_secret")
    redirect_uri = getenv_any(
        "HH_REDIRECT_URI",
        "hh_redirect_uri",
        default="http://localhost:8787/api/channels/hh/oauth/callback",
    )
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "configured": bool(client_id and client_secret and redirect_uri),
    }


def latest_channel_account(channel: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM channel_accounts WHERE channel = ?", (channel,)).fetchone()
    if row is None:
        return None
    account = dict(row)
    account.pop("raw_profile", None)
    return account


def latest_linkedin_account() -> dict[str, Any] | None:
    return latest_channel_account("linkedin")


def token_expires_at(token_response: dict[str, Any]) -> str:
    expires_in = int(token_response.get("expires_in", 0) or 0)
    if expires_in <= 0:
        return ""
    # Keep a small safety margin so API calls do not race token expiry.
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0, expires_in - 60))).isoformat()


def save_channel_token(channel: str, token_response: dict[str, Any]) -> None:
    access_token = str(token_response.get("access_token", "")).strip()
    if not access_token:
        return
    refresh_token = str(token_response.get("refresh_token", "")).strip()
    now = utc_now()
    with connect() as conn:
        existing = conn.execute("SELECT * FROM channel_tokens WHERE channel = ?", (channel,)).fetchone()
        created_at = existing["created_at"] if existing else now
        if not refresh_token and existing:
            refresh_token = str(existing["refresh_token"] or "")
        conn.execute(
            """
            INSERT INTO channel_tokens (
                channel, created_at, updated_at, access_token, refresh_token,
                expires_at, raw_token
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel) DO UPDATE SET
                updated_at = excluded.updated_at,
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                expires_at = excluded.expires_at,
                raw_token = excluded.raw_token
            """,
            (
                channel,
                created_at,
                now,
                access_token,
                refresh_token,
                token_expires_at(token_response),
                json.dumps(token_response, ensure_ascii=False),
            ),
        )


def latest_channel_token(channel: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM channel_tokens WHERE channel = ?", (channel,)).fetchone()
    return dict(row) if row else None


def token_is_current(token: dict[str, Any]) -> bool:
    expires_at = str(token.get("expires_at", "") or "")
    if not expires_at:
        return True
    try:
        return datetime.fromisoformat(expires_at) > datetime.now(timezone.utc)
    except ValueError:
        return False


def hh_access_token() -> str:
    token = latest_channel_token("hh")
    if token and token_is_current(token):
        return str(token["access_token"])

    refresh_token = str((token or {}).get("refresh_token", "") or "")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="HH токен не сохранён. Переподключите HH на вкладке “Каналы”.")

    config = hh_config()
    token_response = request_json(
        HH_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
        },
        provider="HH",
    )
    save_channel_token("hh", token_response)
    return str(token_response["access_token"])


def hh_application_access_token() -> str:
    token = latest_channel_token("hh_app")
    if token and token_is_current(token):
        return str(token["access_token"])

    config = hh_config()
    if not config["client_id"] or not config["client_secret"]:
        raise HTTPException(
            status_code=400,
            detail="Для поиска вакансий HH нужны HH_CLIENT_ID и HH_CLIENT_SECRET для application token.",
        )
    token_response = request_json(
        HH_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
        },
        provider="HH",
    )
    save_channel_token("hh_app", token_response)
    return str(token_response["access_token"])


def hh_public_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    return request_json(
        f"https://api.hh.ru{path}{query}",
        access_token=hh_application_access_token(),
        provider="HH",
    )


def request_json(
    url: str,
    *,
    data: dict[str, str] | None = None,
    access_token: str | None = None,
    provider: str = "API",
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if provider.lower() == "hh":
        headers["User-Agent"] = HH_USER_AGENT
        headers["HH-User-Agent"] = HH_USER_AGENT
    payload = None
    if data is not None:
        payload = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    request = urllib.request.Request(url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=502, detail=f"{provider} API error: {detail}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"{provider} API unavailable: {exc}") from exc
    return json.loads(body)


def request_json_probe(
    path: str,
    *,
    access_token: str | None = None,
    params: dict[str, Any] | None = None,
    label: str,
    note: str = "",
    skipped: str = "",
) -> dict[str, Any]:
    if skipped:
        return {
            "label": label,
            "path": path,
            "ok": False,
            "skipped": True,
            "note": skipped,
        }

    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    headers = {
        "Accept": "application/json",
        "User-Agent": HH_USER_AGENT,
        "HH-User-Agent": HH_USER_AGENT,
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    request = urllib.request.Request(f"https://api.hh.ru{path}{query}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status_code = response.status
            raw_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        raw_body = exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return {
            "label": label,
            "path": path,
            "ok": False,
            "status_code": None,
            "note": note,
            "error": f"HH API unavailable: {exc}",
        }

    try:
        body: Any = json.loads(raw_body)
    except json.JSONDecodeError:
        body = raw_body[:4000]

    request_id = ""
    if isinstance(body, dict):
        request_id = str(body.get("request_id", "") or "")

    return {
        "label": label,
        "path": path,
        "ok": 200 <= status_code < 300,
        "status_code": status_code,
        "note": note,
        "request_id": request_id,
        "body": body,
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/channels/hh/status")
def hh_status() -> dict[str, Any]:
    config = hh_config()
    account = latest_channel_account("hh")
    token = latest_channel_token("hh")
    return {
        "channel": "hh",
        "configured": config["configured"],
        "missing": [
            name
            for name, value in {
                "HH_CLIENT_ID": config["client_id"],
                "HH_CLIENT_SECRET": config["client_secret"],
                "HH_REDIRECT_URI": config["redirect_uri"],
            }.items()
            if not value
        ],
        "redirect_uri": config["redirect_uri"],
        "connected": account is not None,
        "token_saved": token is not None,
        "sync_ready": account is not None and token is not None,
        "applicant_api_supported": False,
        "applicant_api_note": (
            "HH прекратил поддержку соискательского API: работа с резюме и откликами от лица соискателя через API недоступна. "
            "Доступны поиск и просмотр вакансий с токеном приложения."
        ),
        "account": account,
    }


@app.get("/api/channels/hh/diagnostics")
def hh_diagnostics() -> dict[str, Any]:
    status = hh_status()
    probes: list[dict[str, Any]] = []
    recommendations: list[str] = []
    access_token = ""
    try:
        access_token = hh_access_token()
    except HTTPException as exc:
        recommendations.append(str(exc.detail))

    me_body: dict[str, Any] = {}
    if access_token:
        me_probe = request_json_probe(
            "/me",
            access_token=access_token,
            label="Профиль текущего пользователя",
            note="Базовая проверка сохраненного OAuth-токена HH.",
        )
        probes.append(me_probe)
        if me_probe.get("ok") and isinstance(me_probe.get("body"), dict):
            me_body = me_probe["body"]

        resumes_probe = request_json_probe(
            "/resumes/mine",
            access_token=access_token,
            label="Собственные резюме соискателя",
            note="Историческая проверка: HH официально прекратил поддержку соискательского API, 403 здесь ожидаем.",
        )
        probes.append(resumes_probe)
        if not resumes_probe.get("ok"):
            recommendations.append(
                "HH подтвердил прекращение поддержки соискательского API: /resumes/mine, резюме и отклики от лица соискателя недоступны. "
                "Используем локальный импорт/редактирование резюме и письма HH как источник событий."
            )

    employer = me_body.get("employer") if isinstance(me_body.get("employer"), dict) else {}
    manager = me_body.get("manager") if isinstance(me_body.get("manager"), dict) else {}
    employer_id = str(employer.get("id") or me_body.get("employer_id") or "")
    manager_id = str(manager.get("id") or me_body.get("manager_id") or "")
    if access_token and employer_id:
        probes.append(
            request_json_probe(
                f"/employers/{urllib.parse.quote(employer_id)}/services/payable_api_actions/active",
                access_token=access_token,
                label="Активные услуги API для платных методов",
                note="Employer paid API: активные услуги для платных методов резюме.",
            )
        )
    else:
        probes.append(
            request_json_probe(
                "/employers/{employer_id}/services/payable_api_actions/active",
                label="Активные услуги API для платных методов",
                skipped="В /me нет employer_id: текущий HH OAuth авторизован как соискатель, не как менеджер работодателя.",
            )
        )

    if access_token and employer_id and manager_id:
        probes.append(
            request_json_probe(
                (
                    f"/employers/{urllib.parse.quote(employer_id)}"
                    f"/managers/{urllib.parse.quote(manager_id)}/method_access"
                ),
                access_token=access_token,
                label="Доступ менеджера к платным методам",
                note="Employer paid API: проверка групп платного доступа для конкретного менеджера.",
            )
        )
    else:
        probes.append(
            request_json_probe(
                "/employers/{employer_id}/managers/{manager_id}/method_access",
                label="Доступ менеджера к платным методам",
                skipped="В /me нет employer_id/manager_id: проверка доступна только для OAuth менеджера работодателя.",
            )
        )

    if me_body.get("auth_type") == "applicant":
        recommendations.append(
            "Текущий токен имеет auth_type=applicant. После закрытия соискательского API он не дает доступ к резюме/откликам; "
            "для HH остаются вакансии по application token и локальная работа с резюме."
        )

    return {
        "channel": "hh",
        "checked_at": utc_now(),
        "status": status,
        "identity": {
            "auth_type": me_body.get("auth_type", ""),
            "is_applicant": bool(me_body.get("is_applicant")),
            "is_employer": bool(me_body.get("is_employer")),
            "is_application": bool(me_body.get("is_application")),
            "resumes_count": me_body.get("counters", {}).get("resumes_count") if isinstance(me_body.get("counters"), dict) else None,
            "resumes_url": me_body.get("resumes_url", ""),
            "employer_id": employer_id,
            "manager_id": manager_id,
        },
        "probes": probes,
        "recommendations": recommendations,
    }


@app.get("/api/channels/hh/connect")
def hh_connect() -> RedirectResponse:
    config = hh_config()
    if not config["configured"]:
        raise HTTPException(status_code=400, detail="HH OAuth не настроен: нужны client id, client secret и redirect uri")

    state = secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute(
            "INSERT INTO oauth_states (state, channel, created_at) VALUES (?, ?, ?)",
            (state, "hh", utc_now()),
        )

    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": config["client_id"],
            "redirect_uri": config["redirect_uri"],
            "state": state,
        }
    )
    return RedirectResponse(f"{HH_AUTH_URL}?{query}")


@app.get("/api/channels/hh/oauth/callback", response_class=HTMLResponse)
def hh_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> HTMLResponse:
    if error:
        message = html.escape(error_description or error)
        return HTMLResponse(f"<h1>HH connection failed</h1><p>{message}</p>", status_code=400)
    if not code or not state:
        raise HTTPException(status_code=400, detail="HH не вернул code/state")

    with connect() as conn:
        row = conn.execute("SELECT * FROM oauth_states WHERE state = ? AND channel = ?", (state, "hh")).fetchone()
        if row is None:
            raise HTTPException(status_code=400, detail="Некорректный OAuth state")
        conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))

    config = hh_config()
    token_response = request_json(
        HH_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config["redirect_uri"],
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
        },
        provider="HH",
    )
    access_token = token_response.get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail="HH не вернул access_token")
    save_channel_token("hh", token_response)

    profile = request_json(HH_ME_URL, access_token=str(access_token), provider="HH")
    now = utc_now()
    name = " ".join(
        value
        for value in [
            str(profile.get("first_name", "")).strip(),
            str(profile.get("last_name", "")).strip(),
        ]
        if value
    ) or str(profile.get("name", "") or profile.get("id", ""))
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO channel_accounts (
                channel, created_at, updated_at, profile_id, name, email,
                picture_url, profile_url, raw_profile
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel) DO UPDATE SET
                updated_at = excluded.updated_at,
                profile_id = excluded.profile_id,
                name = excluded.name,
                email = excluded.email,
                picture_url = excluded.picture_url,
                profile_url = excluded.profile_url,
                raw_profile = excluded.raw_profile
            """,
            (
                "hh",
                now,
                now,
                str(profile.get("id", "")),
                name,
                str(profile.get("email", "")),
                str(profile.get("photo", "")),
                str(profile.get("alternate_url", "")),
                json.dumps(profile, ensure_ascii=False),
            ),
        )

    safe_name = html.escape(name or "HH profile")
    return HTMLResponse(
        f"""
        <!doctype html>
        <html lang="ru">
        <head>
          <meta charset="utf-8">
          <title>HH подключён</title>
          <script>
            setTimeout(() => {{
              if (window.opener) {{
                window.close();
              }}
            }}, 2500);
          </script>
        </head>
        <body>
          <h1>HH подключён</h1>
          <p>Профиль сохранён: {safe_name}</p>
          <p>Данные сохранены локально в Resume Intel. Можно закрыть это окно и вернуться на вкладку "Каналы".</p>
          <p><a href="http://localhost:5177">Вернуться в приложение</a></p>
        </body>
        </html>
        """
    )


@app.post("/api/channels/hh/sync-resumes")
def hh_sync_resumes() -> dict[str, Any]:
    access_token = hh_access_token()
    try:
        resumes_response = hh_api_get("/resumes/mine", access_token=access_token)
    except HTTPException as exc:
        detail = str(exc.detail)
        if "forbidden" in detail.lower():
            raise HTTPException(
                status_code=403,
                detail=(
                    "HH подтвердил прекращение поддержки соискательского API: /resumes/mine, резюме и отклики "
                    "от лица соискателя через API недоступны. Используйте импорт из файла, локальное редактирование "
                    f"и события из писем HH. Исходный ответ: {detail}"
                ),
            ) from exc
        raise
    items = resumes_response.get("items", resumes_response if isinstance(resumes_response, list) else [])
    if not isinstance(items, list):
        raise HTTPException(status_code=502, detail="HH API вернул неожиданный формат списка резюме")

    synced = []
    errors = []
    for short_resume in items:
        if not isinstance(short_resume, dict):
            continue
        resume_id = str(short_resume.get("id", "") or "")
        if not resume_id:
            continue
        try:
            detail = hh_api_get(f"/resumes/{urllib.parse.quote(resume_id)}", access_token=access_token)
            if not isinstance(detail, dict):
                detail = short_resume
        except HTTPException as exc:
            errors.append({"id": resume_id, "detail": exc.detail})
            detail = short_resume
        saved = save_hh_api_resume({**short_resume, **detail})
        synced.append(
            {
                "id": saved["id"],
                "title": saved["title"],
                "external_id": saved["external_id"],
                "url": saved.get("url", ""),
                "updated_at": saved["updated_at"],
                "api_updated_at": saved.get("api_updated_at", ""),
            }
        )

    return {
        "channel": "hh",
        "synced": len(synced),
        "found": int(resumes_response.get("found", len(items)) or len(items)) if isinstance(resumes_response, dict) else len(items),
        "items": synced,
        "errors": errors,
    }


@app.get("/api/channels/linkedin/status")
def linkedin_status() -> dict[str, Any]:
    config = linkedin_config()
    account = latest_linkedin_account()
    return {
        "channel": "linkedin",
        "configured": config["configured"],
        "missing": [
            name
            for name, value in {
                "LINKEDIN_CLIENT_ID": config["client_id"],
                "LINKEDIN_CLIENT_SECRET": config["client_secret"],
                "LINKEDIN_REDIRECT_URI": config["redirect_uri"],
            }.items()
            if not value
        ],
        "redirect_uri": config["redirect_uri"],
        "scopes": config["scopes"],
        "connected": account is not None,
        "account": account,
    }


@app.get("/api/channels/linkedin/connect")
def linkedin_connect() -> RedirectResponse:
    config = linkedin_config()
    if not config["configured"]:
        raise HTTPException(status_code=400, detail="LinkedIn OAuth не настроен: нужны client id, client secret и redirect uri")

    state = secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute(
            "INSERT INTO oauth_states (state, channel, created_at) VALUES (?, ?, ?)",
            (state, "linkedin", utc_now()),
        )

    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": config["client_id"],
            "redirect_uri": config["redirect_uri"],
            "scope": config["scopes"],
            "state": state,
        }
    )
    return RedirectResponse(f"{LINKEDIN_AUTH_URL}?{query}")


@app.get("/api/channels/linkedin/oauth/callback", response_class=HTMLResponse)
def linkedin_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> HTMLResponse:
    if error:
        message = html.escape(error_description or error)
        return HTMLResponse(f"<h1>LinkedIn connection failed</h1><p>{message}</p>", status_code=400)
    if not code or not state:
        raise HTTPException(status_code=400, detail="LinkedIn не вернул code/state")

    with connect() as conn:
        row = conn.execute("SELECT * FROM oauth_states WHERE state = ? AND channel = ?", (state, "linkedin")).fetchone()
        if row is None:
            raise HTTPException(status_code=400, detail="Некорректный OAuth state")
        conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))

    config = linkedin_config()
    token_response = request_json(
        LINKEDIN_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config["redirect_uri"],
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
        },
        provider="LinkedIn",
    )
    access_token = token_response.get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail="LinkedIn не вернул access_token")

    profile = request_json(LINKEDIN_USERINFO_URL, access_token=str(access_token), provider="LinkedIn")
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO channel_accounts (
                channel, created_at, updated_at, profile_id, name, email,
                picture_url, profile_url, raw_profile
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel) DO UPDATE SET
                updated_at = excluded.updated_at,
                profile_id = excluded.profile_id,
                name = excluded.name,
                email = excluded.email,
                picture_url = excluded.picture_url,
                profile_url = excluded.profile_url,
                raw_profile = excluded.raw_profile
            """,
            (
                "linkedin",
                now,
                now,
                str(profile.get("sub", "")),
                str(profile.get("name", "")),
                str(profile.get("email", "")),
                str(profile.get("picture", "")),
                str(profile.get("profile", "")),
                json.dumps(profile, ensure_ascii=False),
            ),
        )

    safe_name = html.escape(str(profile.get("name") or "LinkedIn profile"))
    return HTMLResponse(
        f"""
        <!doctype html>
        <html lang="ru">
        <head>
          <meta charset="utf-8">
          <title>LinkedIn подключён</title>
          <script>
            setTimeout(() => {{
              if (window.opener) {{
                window.close();
              }}
            }}, 2500);
          </script>
        </head>
        <body>
          <h1>LinkedIn подключён</h1>
          <p>Профиль сохранён: {safe_name}</p>
          <p>Данные сохранены локально в Resume Intel. Можно закрыть это окно и вернуться на вкладку "Каналы".</p>
          <p><a href="http://localhost:5177">Вернуться в приложение</a></p>
        </body>
        </html>
        """
    )


@app.get("/api/cv-types")
def cv_types() -> list[dict[str, Any]]:
    return [
        {
            "slug": item["slug"],
            "title": item["title"],
            "updated_at": datetime.fromtimestamp(float(item.get("updated_at", 0)), timezone.utc).isoformat(),
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


@app.put("/api/cv-types/{slug}/documents/{filename}")
def update_cv_type_document(slug: str, filename: str, payload: CvDocumentInput) -> dict[str, Any]:
    return save_cv_type_document(slug, filename, payload.content)


@app.get("/api/hh-resumes")
def hh_resumes() -> list[dict[str, Any]]:
    return [
        {
            "id": item["id"],
            "title": item["title"],
            "status": item["status"],
            "channel": item["channel"],
            "source": item["source"],
            "external_id": item["external_id"],
            "url": item["url"],
            "notes": item["notes"],
            "source_filename": item["source_filename"],
            "updated_at": item["updated_at"],
            "api_updated_at": item["api_updated_at"],
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


@app.put("/api/hh-resumes/{resume_id}/content")
def update_hh_resume_content(resume_id: str, payload: ResumeContentInput) -> dict[str, Any]:
    item = find_hh_resume_by_id(resume_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Резюме не найдено")
    raw_text = payload.content.rstrip() + "\n"
    if len(raw_text.strip()) < 40:
        raise HTTPException(status_code=422, detail="Слишком мало текста для сохранения резюме")

    channel = str(item.get("channel", "hh"))
    parsed_structure = parse_resume_for_channel(channel, raw_text, str(item.get("title", "")))
    title = str(parsed_structure.get("title", "")).strip() or str(item.get("title", "")).strip()
    now = utc_now()
    updated = {
        **item,
        "title": title,
        "keywords": " ".join(resume_keywords(item, parsed_structure, raw_text, 120)),
        "raw_text": raw_text,
        "parsed_structure": parsed_structure,
        "updated_at": now,
        "notes": f"{item.get('notes', '')}\nОтредактировано вручную {now}".strip(),
    }
    save_hh_resume(updated)
    return hh_resume_detail_payload(updated)


@app.post("/api/hh-resumes/{resume_id}/reparse")
def reparse_hh_resume(resume_id: str) -> dict[str, Any]:
    item = find_hh_resume_by_id(resume_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Резюме не найдено")

    raw_text = str(item.get("raw_text", ""))
    if not raw_text:
        raise HTTPException(status_code=400, detail="У резюме нет сохранённого raw_text для повторного разбора")

    channel = str(item.get("channel", "hh"))
    parsed_structure = parse_resume_for_channel(channel, raw_text, str(item.get("title", "")))
    title = str(parsed_structure.get("title", "")).strip() or str(item.get("title", "")).strip()
    now = utc_now()
    updated = {
        **item,
        "title": title,
        "keywords": " ".join(resume_keywords(item, parsed_structure, raw_text, 120)),
        "parsed_structure": parsed_structure,
        "updated_at": now,
        "notes": f"{item.get('notes', '')}\nПовторно разобрано {now}".strip(),
    }
    save_hh_resume(updated)
    return hh_resume_detail_payload(updated)


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

    parsed_structure = parse_resume_for_channel(channel, text, title.strip() or first_content_line(text) or Path(filename).stem)
    detected_title = title.strip() or str(parsed_structure.get("title", "")).strip() or first_content_line(text) or Path(filename).stem
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
            "parsed_structure": parsed_structure,
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
    decoded = decode_native_mail_message(message)
    subject = decoded["subject"]
    sender = decoded["sender"]
    sent_at = decoded["sent_at"]
    body = decoded["body"]

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


@app.post("/api/vacancies/import/native-mail")
def import_vacancy_digest_native_mail(message: NativeMailInput) -> dict[str, Any]:
    decoded = decode_native_mail_message(message)
    subject = decoded["subject"]
    sender = decoded["sender"]
    sent_at = decoded["sent_at"]
    body = decoded["body"]
    if not body:
        raise HTTPException(status_code=400, detail="Пустое письмо")

    digest = parse_vacancy_digest(subject, sender, body)
    source = digest["source"]
    resume_title = digest["resume_title"]
    recommended_profile = resolve_recommended_profile(resume_title)
    now = utc_now()
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
                "recommended_jobs",
                subject,
                sender,
                sent_at,
                "",
                resume_title,
                0.85 if digest["vacancies"] else 0.35,
                body,
                decoded["raw_filename"],
            ),
        )
        event_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM email_events WHERE id = ?", (event_id,)).fetchone()

    saved = []
    errors = []
    recommendation_fields = {
        "import_event_id": event_id,
        **recommended_profile,
    }
    for item in digest["vacancies"]:
        title = item["title"]
        company = item["company"]
        salary = item["salary"]
        parsed_description = item.get("description", "")
        try:
            enriched = enrich_hh_digest_vacancy(title, company) if source == "hh" else None
            if enriched:
                enriched_payload = hh_vacancy_payload(enriched)
                raw_meta = json.loads(enriched_payload.get("raw_json") or "{}")
                raw_meta.update(
                    {
                        "mail_subject": subject,
                        "raw_filename": decoded["raw_filename"],
                        "recommended_resume_title": resume_title,
                        "import_event_id": event_id,
                    }
                )
                enriched_payload["raw_json"] = json.dumps(raw_meta, ensure_ascii=False)
                saved.append(save_company_vacancy({**enriched_payload, **recommendation_fields}))
            else:
                saved.append(
                    save_company_vacancy(
                        {
                            "source": source if source != "unknown" else "mail_digest",
                            "company_name": company or "Компания не определена",
                            "employer_name": company or "Компания не определена",
                            "title": title,
                            "salary": salary,
                            "description": "\n".join(part for part in [title, company, salary, parsed_description, resume_title, subject] if part),
                            "raw_json": json.dumps(
                                {
                                    "mail_subject": subject,
                                    "raw_filename": decoded["raw_filename"],
                                    "recommended_resume_title": resume_title,
                                    "import_event_id": event_id,
                                    "parsed": item,
                                },
                                ensure_ascii=False,
                            ),
                            **recommendation_fields,
                        }
                    )
                )
        except Exception as exc:
            errors.append({"title": title, "company": company, "error": str(exc)})

    return {
        "event": row_to_event(row),
        "resume_title": resume_title,
        "recommended_profile": build_recommended_profile(recommendation_fields),
        "parsed": len(digest["vacancies"]),
        "saved": saved,
        "errors": errors,
    }


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


@app.get("/api/channels/hh/vacancies/search")
def hh_vacancy_search(
    text: str = "",
    company: str = "",
    area: str = "",
    page: int = 0,
    per_page: int = 20,
) -> dict[str, Any]:
    query_text = " ".join(part for part in [company.strip(), text.strip()] if part)
    if not query_text:
        raise HTTPException(status_code=400, detail="Укажите текст поиска или компанию")
    params: dict[str, Any] = {
        "text": query_text,
        "page": max(0, page),
        "per_page": min(max(1, per_page), 50),
    }
    if area:
        params["area"] = area
    result = hh_public_get("/vacancies", params)
    items = result.get("items", []) if isinstance(result, dict) else []
    normalized_items = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        payload = hh_vacancy_payload(item)
        payload["snippet"] = item.get("snippet", {})
        payload["employer"] = item.get("employer", {})
        normalized_items.append(payload)
    return {
        "source": "hh_api",
        "query": query_text,
        "found": result.get("found", len(normalized_items)) if isinstance(result, dict) else len(normalized_items),
        "page": result.get("page", page) if isinstance(result, dict) else page,
        "pages": result.get("pages", 1) if isinstance(result, dict) else 1,
        "items": normalized_items,
    }


@app.get("/api/channels/hh/vacancies/{vacancy_id}")
def hh_vacancy_detail(vacancy_id: str) -> dict[str, Any]:
    vacancy = hh_public_get(f"/vacancies/{urllib.parse.quote(vacancy_id)}")
    return {
        **hh_vacancy_payload(vacancy),
        "raw": vacancy,
    }


@app.post("/api/channels/hh/vacancies/{vacancy_id}/save")
def save_hh_vacancy(vacancy_id: str) -> dict[str, Any]:
    vacancy = hh_public_get(f"/vacancies/{urllib.parse.quote(vacancy_id)}")
    return save_company_vacancy(hh_vacancy_payload(vacancy))


@app.get("/api/channels/hh/employers/{employer_id}")
def hh_employer_detail(employer_id: str) -> dict[str, Any]:
    return hh_public_get(f"/employers/{urllib.parse.quote(employer_id)}")


@app.post("/api/vacancies")
def add_vacancy(vacancy: VacancyInput) -> dict[str, Any]:
    return save_company_vacancy(
        {
            "source": "manual",
            "company_name": vacancy.company,
            "employer_name": vacancy.company,
            "title": vacancy.title,
            "url": vacancy.url or "",
            "description": vacancy.description,
        }
    )


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
    return [vacancy_payload(dict(row)) for row in rows]
