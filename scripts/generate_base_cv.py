#!/usr/bin/env python3
"""Generate a neutral base CV markdown file from structured CV data."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "data" / "profile.json"
BASE_CV_PATH = ROOT / "data" / "base_cv.json"
OUTPUT_PATH = ROOT / "output" / "base_cv" / "base_cv.md"


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


def bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def render_experience(base_cv: dict) -> str:
    sections = []
    for item in base_cv["experience"]:
        sections.append(
            dedent(
                f"""\
                ### {item["period"]} — {item["company"]}
                **{item["role"]}**

                {bullet_list(item["default_bullets"])}
                """
            )
        )
    return "\n\n".join(sections)


def render_base_cv(profile: dict, base_cv: dict) -> str:
    candidate = profile["candidate"]
    contacts = base_cv["contacts"]
    headline = candidate.get("base_headline") or " / ".join(candidate["target_titles"])
    return dedent(
        f"""\
        # {candidate["name"]}

        **{headline}**

        {candidate["location"]} · {contacts["phone"]} · {contacts["email"]} · LinkedIn: {contacts["linkedin"]} · Telegram: {contacts["telegram"]}

        ## Профессиональный профиль

        {candidate["positioning"]}

        ## Ключевой опыт и достижения

        {bullet_list(candidate["core_evidence"])}

        ## Профессиональный опыт

        {render_experience(base_cv)}

        ## Образование

        {bullet_list(base_cv["education"])}

        ## Дополнительное обучение и сертификации

        {bullet_list(base_cv["additional_training"])}
        """
    )


def main() -> None:
    profile = load_json(PROFILE_PATH)
    base_cv = load_json(BASE_CV_PATH)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(normalize_markdown(render_base_cv(profile, base_cv)), encoding="utf-8")
    print(f"Generated base CV: {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
