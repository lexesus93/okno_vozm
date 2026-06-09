# CV Vacancy Toolkit

Минимальный тулсет для полуавтоматической адаптации CV под вакансии.

## Структура

- `data/profile.json` — мастер-профиль кандидата и 4 версии позиционирования CV.
- `data/base_cv.json` — базовые разделы CV и track-specific bullets для разных типов вакансий.
- `data/vacancies.json` — реестр вакансий: требования, ключевые слова, риски и доказательства совпадения.
- `scripts/generate_base_cv.py` — генератор базового CV в Markdown.
- `scripts/generate_application_pack.py` — генератор черновиков отклика.
- `scripts/generate_tailored_cv.py` — генератор кастомизированных CV-черновиков.
- `output/base_cv/` — базовое CV в Markdown.
- `output/applications/` — сгенерированные пакеты по вакансиям; каждая вакансия хранится в отдельной папке по ID.
- `output/cv_types/` — 3 переиспользуемых типа CV и supporting-документы.
- `apps/resume-intel/` — локальный MVP для разбора писем о внимании к резюме и сопоставления с CV-типами.

## Быстрый старт

Сгенерировать 3 самых приоритетных отклика:

```bash
python3 scripts/generate_application_pack.py --top 3
```

Сгенерировать базовое CV в Markdown:

```bash
python3 scripts/generate_base_cv.py
```

Сгенерировать конкретные вакансии:

```bash
python3 scripts/generate_application_pack.py --ids 133105818 132986018 133059201
```

Сгенерировать кастомизированные CV по тем же вакансиям:

```bash
python3 scripts/generate_tailored_cv.py --ids 133105818 132986018 133059201
```

## Что создается по каждой вакансии

- `analysis.md` — короткий анализ совпадения, требования, риски.
- `cv_patch.md` — что поменять в CV: заголовок, summary, skills, bullets.
- `tailored_cv.md` — готовый самостоятельный CV-черновик с примененными акцентами.
- `cover_letter.md` — черновик сопроводительного сообщения.
- `interview_prep.md` — вопросы работодателю и риски для репетиции.

## Как использовать дальше

1. Добавить или уточнить вакансию в `data/vacancies.json`.
2. Запустить генератор по ID вакансии.
3. Открыть `output/applications/<id>-<slug>/`.
4. Отредактировать `tailored_cv.md`, `cover_letter.md` и при необходимости свериться с `cv_patch.md`.
5. Перенести финальную версию CV в DOCX/PDF.

Это первая версия без внешних API. Следующий шаг автоматизации — добавить импорт markdown-файла вакансии с hh.ru и автозаполнение черновой карточки вакансии.

## Resume Intel MVP

Локальное приложение для писем из Mail / hh.ru / LinkedIn:

```bash
cd apps/resume-intel
docker compose up --build
```

UI будет доступен на `http://localhost:5177`. Подробности: `apps/resume-intel/README.md`.

Для прямого drag-and-drop из Apple Mail используйте Electron-режим:

```bash
cd apps/resume-intel
docker compose up -d backend
cd frontend
npm install
npm run electron:dev
```
