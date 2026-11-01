# FaceMetric

Веб-платформа объективной оценки внешности (ниша looks-max) от **VA digital studio**.

Сервис анализирует пропорции лица по фотографии: извлекает антропометрические
landmark-точки, считает набор метрик (симметрия, canthal tilt, трети лица, jawline,
диморфизм и др.), переводит отклонения от популяционных эталонов в баллы 0–10 и
формирует отчёт с графиками и рекомендациями. Дополнительно: мобатлы с ELO,
упражнения, ИИ-консультант, прогресс и достижения.

> **Дисклеймер.** Оценка построена на математической модели пропорций лица и
> популяционных ориентирах. Это не медицинское заключение и не объективная истина
> о внешности. Результаты зависят от качества фото, освещения и ракурса.

## Стек

| Слой | Технологии |
|------|-----------|
| Backend | FastAPI (async), Uvicorn, SQLAlchemy 2.0 (async), Alembic, Pydantic v2, pydantic-settings |
| Аутентификация | JWT (PyJWT, access 15 мин + refresh 7 дней), argon2-cffi, httpOnly cookies, slowapi |
| БД | PostgreSQL + asyncpg (prod), SQLite + aiosqlite (локально) |
| ML / CV | InsightFace (landmarks + genderage), MediaPipe (fallback), OpenCV, NumPy, scikit-learn |
| ИИ | LLM через API (OpenAI/OpenRouter/Anthropic), httpx async, SSE-стриминг |
| Фронтенд | Jinja2, HTML/CSS, vanilla JS, Canvas API, Chart.js (CDN) |
| Инфра | pytest + pytest-asyncio, ruff, mypy, Docker + docker-compose, structlog, Sentry (опц.) |

### Почему InsightFace основной, а MediaPipe — fallback

* **InsightFace (buffalo_l)** даёт 106 точек (`2d106det`) с точной разметкой контура
  челюсти и уголков глаз — это критично для `gonial angle`, `bigonial width` и
  `canthal tilt`. Плюс встроенный `genderage` даёт независимую оценку пола/возраста,
  нужную для метрики полового диморфизма (не только геометрия).
* **MediaPipe FaceMesh (468 точек)** легче и не требует скачивания весов, но хуже
  калиброван по антропометрии и не даёт пол/возраст. Поэтому он — резервный
  провайдер: если InsightFace недоступен, сервис продолжает работать.
* Обе модели грузятся **лениво** и кэшируются в памяти процесса (`ML_LAZY_LOAD`).

### Почему async

FastAPI + async SQLAlchemy: запросы к БД, загрузка/обработка изображений и
стриминг ответов LLM не блокируют event loop. Тяжёлые CPU-операции CV выполняются
в пуле воркеров через `run_in_threadpool` (см. API фото на этапе 3), чтобы не
тормозить обработку других запросов.

## Структура проекта

```
facemetric/
├── app/
│   ├── main.py              # FastAPI-приложение, health-check, error handler
│   ├── config.py            # pydantic-settings, .env
│   ├── db.py                # async engine, Base, сессии
│   ├── logging_config.py    # structlog (JSON в prod)
│   ├── models/              # SQLAlchemy: user, photo, report, battle, exercise, chat, ...
│   ├── schemas/             # Pydantic-схемы
│   ├── api/                 # роутеры: auth, rate, battles, exercises, chat, profile, admin, ...
│   ├── core/                # security (JWT/argon2), deps, storage (magic bytes)
│   ├── ml/                  # landmarks, preprocessing, metrics, scoring, constants
│   ├── ai/                  # LLM-консультант, промты, SSE
│   ├── templates/           # Jinja2
│   └── static/              # CSS / JS / img
├── alembic/                 # миграции
├── tests/                   # pytest
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

## Быстрый старт (локально, SQLite)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,ml]"        # без ML: pip install -e ".[dev]"
cp .env.example .env              # при необходимости отредактируйте
alembic upgrade head
uvicorn app.main:app --reload
```

Откройте http://localhost:8000/health — health-check покажет статус БД и ML-провайдеров.

## Запуск в Docker (Postgres)

```bash
docker compose up --build
```

Приложение поднимется на http://localhost:8000, PostgreSQL — на 5432.
Миграции применяются автоматически перед стартом.

## Разработка

```bash
ruff check app && ruff format --check app
mypy app
pytest
```

## Метрики и источники

Эталоны, допуски и веса метрик собраны в `app/ml/constants.py` — каждое значение
сопровождается ссылкой на источник (Naini, Farkas, Ricketts, Powell & Humphreys,
Pallett, Little, Perrett и др.). Веса можно переопределять из админки
(таблица `metric_weights`), `ml/scoring.py` учитывает их при расчёте.

## Приватность и этика

* Явное согласие на обработку биометрии при регистрации.
* Эндпоинт удаления аккаунта и всех данных (GDPR-подобно).
* Фото изолированы по пользователю; публикация в батлах — только opt-in.
* В отчётах нет токсичных формулировок; дисклеймер выводится всегда.

## Этапы разработки

- [x] **Этап 1.** Архитектура, структура, pyproject, config, модели БД, Alembic, README.
- [ ] Этап 2. Аутентификация (JWT + argon2), главная, базовый UX.
- [ ] Этап 3. Загрузка фото → preprocessing → landmarks → canvas drag&drop.
- [ ] Этап 4. Метрики + агрегация + отчёт (JSON/HTML/Chart.js/PDF).
- [ ] Этап 5. Профиль, история, прогресс, до/после, достижения, streak, публичная ссылка.
- [ ] Этап 6. Мобатлы (ELO, лидерборд, батл недели, анти-чит).
- [ ] Этап 7. Упражнения + ИИ-чат (SSE, быстрые промты, looksmax-план).
- [ ] Этап 8. Админка (дашборд, юзеры, модерация, CRUD, веса, логи, фидбек).
- [ ] Этап 9. Контент (блог, FAQ, метрики, о нас, приватность, условия), SEO, аналитика, health.
- [ ] Этап 10. pytest, ruff/mypy, Docker, финальный README, онбординг-тур, полировка.

## Лицензия

Proprietary. © VA digital studio.
