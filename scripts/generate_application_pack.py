#!/usr/bin/env python3
"""Generate first-draft application packs from a structured CV profile and vacancies."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from textwrap import dedent


ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "data" / "profile.json"
VACANCIES_PATH = ROOT / "data" / "vacancies.json"
OUTPUT_DIR = ROOT / "output" / "applications"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def numbered_list(items: list[str]) -> str:
    return "\n".join(f"{idx}. {item}" for idx, item in enumerate(items, start=1))


def normalize_markdown(content: str) -> str:
    """Flush template indentation introduced by nested Python blocks."""
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


def render_analysis(profile: dict, vacancy: dict, track: dict) -> str:
    candidate = profile["candidate"]
    return dedent(
        f"""\
        # Анализ совпадения: {vacancy["title"]} — {vacancy["company"]}

        Дата генерации: {date.today().isoformat()}

        Ссылка на вакансию: {vacancy["url"]}

        ## Короткий вывод

        Приоритет: {vacancy["priority_score"]}/100

        {vacancy["interest_note"]}

        Рекомендуемый трек CV: **{track["headline"]}**.

        ## Почему профиль подходит

        {bullet_list(vacancy["evidence"])}

        ## Требования вакансии

        {bullet_list(vacancy["requirements"])}

        ## Риски и как их закрывать

        {bullet_list(vacancy["risks"])}

        ## Базовое позиционирование кандидата

        {candidate["positioning"]}
        """
    )


def render_cv_patch(vacancy: dict, track: dict) -> str:
    relevant_keywords = vacancy.get("cv_keywords")
    if relevant_keywords is None:
        relevant_keywords = []
        for keyword in track["keywords"] + vacancy["keywords"]:
            if keyword not in relevant_keywords:
                relevant_keywords.append(keyword)

    top_evidence = vacancy["evidence"][:4]
    headline = vacancy.get("cv_headline", track["headline"])
    summary = vacancy.get("cv_summary", track["summary"])

    return dedent(
        f"""\
        # Черновик адаптации CV: {vacancy["title"]} — {vacancy["company"]}

        ## Заголовок CV

        {headline}

        ## Summary для верхнего блока

        {summary}

        ## Ключевые навыки для этой вакансии

        {", ".join(relevant_keywords)}

        ## Какие bullet points поднять выше

        {bullet_list(top_evidence)}

        ## Что добавить или переформулировать

        {bullet_list([
            "В первом экране CV использовать терминологию вакансии, но не добавлять неподтвержденный опыт.",
            "В IBM-блоке сильнее раскрыть Data & AI, enterprise delivery, P&L, банковских заказчиков и масштаб команд.",
            "В Astra-блоке показать запуск направления с нуля как доказательство способности строить функцию, процессы и команду.",
            "В Форсайт/аккаунт-блоках оставить только то, что усиливает конкретную роль: C-level, стратегические заказчики, коммерческий результат.",
        ])}
        """
    )


def render_cover_letter(vacancy: dict, track: dict) -> str:
    evidence = vacancy["evidence"][:3]
    public_gap = vacancy.get("public_gap", "")
    risk_bridge = f"Отдельно отмечу: {public_gap}" if public_gap else ""
    extra_focus = vacancy.get("cover_letter_extra", "")
    after_evidence = vacancy.get("cover_letter_after_evidence", "")
    focus = vacancy.get("cover_letter_focus", track["title"])
    profile_paragraph = vacancy.get(
        "cover_letter_profile",
        "У меня более 20 лет управленческого опыта в ИТ, Data & AI, консалтинге и сложных enterprise-проектах. В IBM я отвечал за направление профессиональных услуг Data & AI в регионе, управлял P&L, распределенными командами и проектами для крупнейших банков, телеком- и корпоративных заказчиков. В Группе Астра запускал консалтинговое направление с нуля: бизнес-модель, процессы, команду и первые проекты 100+ млн рублей.",
    )

    return dedent(
        f"""\
        Здравствуйте.

        Меня заинтересовала вакансия «{vacancy["title"]}» в {vacancy["company"]}, потому что она хорошо совпадает с моим опытом в области {focus}.

        {profile_paragraph}

        {extra_focus}

        Для вашей задачи особенно релевантны:
        {bullet_list(evidence)}

        {after_evidence}

        {risk_bridge}

        Буду рад обсудить, как мой опыт может быть полезен для задач этой роли.

        Алексей Матвеев
        """
    )


def render_interview_prep(vacancy: dict) -> str:
    questions = [
        f"Какой главный бизнес-результат ожидается от роли «{vacancy['title']}» в первые 6-12 месяцев?",
        "Какие инициативы уже запущены, а какие нужно будет формировать с нуля?",
        "Как устроено взаимодействие роли с бизнес-заказчиками, ИТ, продуктом и топ-менеджментом?",
        "Какие метрики успеха будут считаться ключевыми?",
        "Какие ограничения сейчас самые болезненные: люди, процессы, архитектура, бюджет, данные или скорость принятия решений?",
    ]

    risk_questions = [f"Как отвечать на риск: {risk}" for risk in vacancy["risks"]]

    return dedent(
        f"""\
        # Подготовка к интервью: {vacancy["title"]} — {vacancy["company"]}

        ## Вопросы работодателю

        {numbered_list(questions)}

        ## Риски, которые надо отрепетировать

        {bullet_list(risk_questions)}

        ## Слова-маркеры вакансии

        {", ".join(vacancy["keywords"])}
        """
    )


def render_index(generated: list[tuple[dict, Path]]) -> str:
    rows = [
        f"- `{vacancy['id']}` — {vacancy['company']}, {vacancy['title']}: {vacancy['url']} — `{path.relative_to(ROOT)}`"
        for vacancy, path in generated
    ]
    return dedent(
        f"""\
        # Сгенерированные пакеты откликов

        Дата генерации: {date.today().isoformat()}

        {bullet_list(["Каждая папка содержит analysis.md, cv_patch.md, cover_letter.md и interview_prep.md."])}

        ## Пакеты

        {chr(10).join(rows)}
        """
    )


def render_vacancy_links(vacancies: list[dict]) -> str:
    rows = [
        f"- `{vacancy['id']}` — {vacancy['company']}, {vacancy['title']}: {vacancy['url']}"
        for vacancy in sorted(vacancies, key=lambda item: item["priority_score"], reverse=True)
    ]
    return dedent(
        f"""\
        # Ссылки на вакансии

        ## Все вакансии из реестра

        {chr(10).join(rows)}
        """
    )


def write_pack(profile: dict, vacancy: dict) -> Path:
    track = profile["tracks"][vacancy["track"]]
    target_dir = OUTPUT_DIR / f"{vacancy['id']}-{vacancy['slug']}"
    target_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "analysis.md": render_analysis(profile, vacancy, track),
        "cv_patch.md": render_cv_patch(vacancy, track),
        "cover_letter.md": render_cover_letter(vacancy, track),
        "interview_prep.md": render_interview_prep(vacancy),
    }

    for filename, content in files.items():
        content = apply_text_replacements(content, vacancy)
        (target_dir / filename).write_text(normalize_markdown(content), encoding="utf-8")

    return target_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate CV adaptation and cover-letter drafts.")
    parser.add_argument("--ids", nargs="+", help="Vacancy IDs to generate, e.g. --ids 133105818 132986018")
    parser.add_argument("--top", type=int, help="Generate N highest-priority vacancies")
    args = parser.parse_args()

    profile = load_json(PROFILE_PATH)
    vacancies = load_json(VACANCIES_PATH)
    selected = select_vacancies(vacancies, args.ids, args.top)

    generated = [(vacancy, write_pack(profile, vacancy)) for vacancy in selected]
    (OUTPUT_DIR / "index.md").write_text(normalize_markdown(render_index(generated)), encoding="utf-8")
    (OUTPUT_DIR / "vacancy_links.md").write_text(normalize_markdown(render_vacancy_links(vacancies)), encoding="utf-8")

    print(f"Generated {len(generated)} application pack(s):")
    for vacancy, path in generated:
        print(f"- {vacancy['id']} {vacancy['company']}: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
