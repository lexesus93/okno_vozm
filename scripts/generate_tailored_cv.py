#!/usr/bin/env python3
"""Generate tailored CV drafts for selected vacancies."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from textwrap import dedent


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "data" / "profile.json"
BASE_CV_PATH = ROOT / "data" / "base_cv.json"
VACANCIES_PATH = ROOT / "data" / "vacancies.json"
APPLICATIONS_DIR = ROOT / "output" / "applications"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def normalize_markdown(content: str) -> str:
    lines = [line.strip() if line.strip() else "" for line in content.splitlines()]
    normalized: list[str] = []
    for line in lines:
        if line or (normalized and normalized[-1]):
            normalized.append(line)
    return "\n".join(normalized).strip() + "\n"


def apply_text_replacements(content: str, vacancy: dict) -> str:
    for old, new in vacancy.get("text_replacements", []):
        content = content.replace(old, new)
    return content


def bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def select_vacancies(vacancies: list[dict], ids: list[str] | None, top: int | None) -> list[dict]:
    if ids:
        wanted = set(ids)
        selected = [vacancy for vacancy in vacancies if vacancy["id"] in wanted]
        missing = wanted - {vacancy["id"] for vacancy in selected}
        if missing:
            raise SystemExit(f"Не найдены вакансии: {', '.join(sorted(missing))}")
        return selected

    ranked = sorted(vacancies, key=lambda item: item["priority_score"], reverse=True)
    return ranked[: top or 3]


def merged_keywords(track: dict, vacancy: dict) -> list[str]:
    if "cv_keywords" in vacancy:
        return vacancy["cv_keywords"]

    result = []
    for keyword in track["keywords"] + vacancy["keywords"]:
        if keyword not in result:
            result.append(keyword)
    return result


def dedupe(items: list[str]) -> list[str]:
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def render_experience(base_cv: dict, track_key: str, vacancy: dict) -> str:
    sections = []
    overrides = vacancy.get("experience_overrides", {})
    for item in base_cv["experience"]:
        override = overrides.get(item["company"], {})
        role = override.get("role", item["role"])

        if "bullets" in override:
            bullets = override["bullets"]
        else:
            bullets = (
                override.get("prepend_bullets", [])
                + item["default_bullets"]
                + item.get("track_bullets", {}).get(track_key, [])
                + override.get("append_bullets", [])
            )
        bullets = dedupe(bullets)
        sections.append(
            dedent(
                f"""\
                ### {item["period"]} — {item["company"]}
                **{role}**

                {bullet_list(bullets)}
                """
            )
        )
    return "\n\n".join(sections)


def render_tailored_cv(profile: dict, base_cv: dict, vacancy: dict) -> str:
    candidate = profile["candidate"]
    track_key = vacancy["track"]
    track = profile["tracks"][track_key]
    contacts = base_cv["contacts"]
    skills = merged_keywords(track, vacancy)
    headline = vacancy.get("cv_headline", track["headline"])
    summary = vacancy.get("cv_summary", track["summary"])
    profile_extra = vacancy.get(
        "cv_profile_extra",
        "Мой опыт наиболее релевантен там, где нужно соединить стратегию, технологическую экспертизу, управление командами, работу с руководителями бизнеса и достижение измеримого эффекта.",
    )

    return dedent(
        f"""\
        # {candidate["name"]}

        **{headline}**

        Москва, Россия · {contacts["phone"]} · {contacts["email"]} · LinkedIn: {contacts["linkedin"]} · Telegram: {contacts["telegram"]}

        ## Профессиональный профиль

        {summary}

        {profile_extra}

        ## Ключевые компетенции

        {", ".join(skills)}

        ## Релевантный опыт и достижения

        {bullet_list(vacancy["evidence"])}

        ## Профессиональный опыт

        {render_experience(base_cv, track_key, vacancy)}

        ## Образование

        {bullet_list(base_cv["education"])}

        ## Дополнительное обучение и сертификации

        {bullet_list(base_cv["additional_training"])}
        """
    )


def render_index(generated: list[tuple[dict, Path]]) -> str:
    rows = [
        f"- `{vacancy['id']}` — {vacancy['company']}, {vacancy['title']}: `{path.relative_to(ROOT)}`"
        for vacancy, path in generated
    ]
    return dedent(
        f"""\
        # Кастомизированные CV в пакетах откликов

        Дата генерации: {date.today().isoformat()}

        ## Файлы

        {chr(10).join(rows)}
        """
    )


def write_tailored_cv(profile: dict, base_cv: dict, vacancy: dict) -> Path:
    target_dir = APPLICATIONS_DIR / f"{vacancy['id']}-{vacancy['slug']}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / "tailored_cv.md"
    content = apply_text_replacements(render_tailored_cv(profile, base_cv, vacancy), vacancy)
    target_path.write_text(normalize_markdown(content), encoding="utf-8")
    return target_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate tailored CV markdown drafts.")
    parser.add_argument("--ids", nargs="+", help="Vacancy IDs to generate, e.g. --ids 133105818 132986018")
    parser.add_argument("--top", type=int, help="Generate N highest-priority vacancies")
    args = parser.parse_args()

    profile = load_json(PROFILE_PATH)
    base_cv = load_json(BASE_CV_PATH)
    vacancies = load_json(VACANCIES_PATH)
    selected = select_vacancies(vacancies, args.ids, args.top)

    generated = [(vacancy, write_tailored_cv(profile, base_cv, vacancy)) for vacancy in selected]
    (APPLICATIONS_DIR / "tailored_cv_index.md").write_text(normalize_markdown(render_index(generated)), encoding="utf-8")

    print(f"Generated {len(generated)} tailored CV draft(s):")
    for vacancy, path in generated:
        print(f"- {vacancy['id']} {vacancy['company']}: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
