"""Роутер HTML-страниц (Jinja2). Лендинг, вход, регистрация, восстановление пароля.

Статические страницы контента (блог, о метриках, о нас, приватность, условия,
контакты) добавляются на этапе 9.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.config import BASE_DIR, settings
from app.core.deps import OptionalUser
from app.db import SessionLocal
from app.models.battle import Vote
from app.models.report import Report
from app.models.user import User

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

# Замоканные отзывы — заменяются на реальные по мере наполнения.
TESTIMONIALS: list[dict[str, str]] = [
    {
        "text": "Наконец-то не «оцените меня», а конкретные цифры и что с ними делать.",
        "author": "Артём, 27",
    },
    {
        "text": "Сравнение «до/после» за 3 месяца реально мотивирует держать режим.",
        "author": "Ника, 24",
    },
    {
        "text": "ИИ-консультант без воды собрал план по jawline и уходу.",
        "author": "Даниил, 31",
    },
]


def _base_context(request: Request, user: Any = None, **extra: Any) -> dict[str, Any]:
    """Базовый контекст шаблонов: бренд, год, пользователь, base_url."""
    ctx: dict[str, Any] = {
        "app_name": settings.app_name,
        "base_url": settings.base_url,
        "current_year": datetime.now(UTC).year,
        "request": request,
        "user": user,
        "onboarding": bool(user),
    }
    ctx.update(extra)
    return ctx


async def _platform_stats() -> dict[str, int | float]:
    """Собирает счётчики для лендинга из БД."""
    async with SessionLocal() as db:
        users = (await db.execute(select(func.count(User.id)))).scalar_one()
        reports = (await db.execute(select(func.count(Report.id)))).scalar_one()
        votes = (await db.execute(select(func.count(Vote.id)))).scalar_one()
        avg = (await db.execute(select(func.avg(Report.overall_score)))).scalar_one()
    return {
        "users": int(users or 0),
        "reports": int(reports or 0),
        "battles": int(votes or 0),
        "metrics": 17,
        "avg_score": round(float(avg), 1) if avg else 7.4,
    }


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request, user: OptionalUser) -> HTMLResponse:
    """Главная страница."""
    stats = await _platform_stats()
    faq = [
        {
            "question": "Насколько точна оценка?",
            "answer_html": "Модель измеряет геометрические пропорции лица и сравнивает их "
            "с популяционными ориентирами из антропометрии. Это формализация, а не истина: "
            "результат зависит от фото, света и ракурса.",
        },
        {
            "question": "Что происходит с моими фото?",
            "answer_html": "Файлы хранятся изолированно и доступны только вам. Публикация "
            "в батлах — только по вашему opt-in. Аккаунт можно удалить вместе со всеми данными.",
        },
        {
            "question": "Это бесплатно?",
            "answer_html": "Да, все функции платформы бесплатны: оценка, отчёты, батлы, "
            "упражнения и ИИ-консультант.",
        },
        {
            "question": "Это медицинский сервис?",
            "answer_html": "Нет. FaceMetric не ставит диагнозы и не заменяет врача. "
            "Мы даём только измеримую геометрию и общие рекомендации.",
        },
    ]
    return templates.TemplateResponse(
        request,
        "index.html",
        _base_context(request, user, stats=stats, faq=faq, testimonials=TESTIMONIALS),
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user: OptionalUser) -> Response:
    """Страница входа. Авторизованных отправляем в профиль."""
    if user:
        return RedirectResponse("/profile", status_code=302)
    return templates.TemplateResponse(request, "login.html", _base_context(request, user))


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, user: OptionalUser) -> Response:
    """Страница регистрации."""
    if user:
        return RedirectResponse("/profile", status_code=302)
    return templates.TemplateResponse(request, "register.html", _base_context(request, user))


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, user: OptionalUser) -> HTMLResponse:
    """Страница запроса восстановления пароля."""
    return templates.TemplateResponse(request, "reset_password.html", _base_context(request, user))


@router.get("/rate", response_class=HTMLResponse)
async def rate_page(request: Request, user: OptionalUser) -> Response:
    """Страница оценки: загрузка фото и редактор точек. Требует входа."""
    if user is None:
        return RedirectResponse("/login?next=/rate", status_code=302)
    return templates.TemplateResponse(request, "rate.html", _base_context(request, user))


@router.get("/report", response_class=HTMLResponse)
async def report_page(request: Request, user: OptionalUser) -> Response:
    """Страница отчёта. Гостя отправляем на вход."""
    if user is None:
        return RedirectResponse("/login?next=/report", status_code=302)
    return templates.TemplateResponse(request, "report.html", _base_context(request, user))


@router.get("/r/{token}", response_class=HTMLResponse)
async def public_report_page(request: Request, token: str, user: OptionalUser) -> Response:
    """Публичная страница отчёта по анонимному токену."""
    context = _base_context(request, user)
    context["public_token"] = token
    return templates.TemplateResponse(request, "report.html", context)


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, user: OptionalUser) -> Response:
    """Страница профиля: статистика, прогресс, до/после, достижения."""
    if user is None:
        return RedirectResponse("/login?next=/profile", status_code=302)
    return templates.TemplateResponse(request, "profile.html", _base_context(request, user))


@router.get("/battles", response_class=HTMLResponse)
async def battles_page(request: Request, user: OptionalUser) -> Response:
    """Страница мобатлов: пара фото, голосование, лидерборд."""
    if user is None:
        return RedirectResponse("/login?next=/battles", status_code=302)
    return templates.TemplateResponse(request, "battles.html", _base_context(request, user))


@router.get("/exercises", response_class=HTMLResponse)
async def exercises_page(request: Request, user: OptionalUser) -> Response:
    """Страница библиотеки упражнений с прогрессом."""
    if user is None:
        return RedirectResponse("/login?next=/exercises", status_code=302)
    return templates.TemplateResponse(request, "exercises.html", _base_context(request, user))


@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request, user: OptionalUser) -> Response:
    """Страница ИИ-консультанта."""
    if user is None:
        return RedirectResponse("/login?next=/chat", status_code=302)
    return templates.TemplateResponse(request, "chat.html", _base_context(request, user))


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, user: OptionalUser) -> Response:
    """Страница админки (только для администраторов)."""
    if user is None:
        return RedirectResponse("/login?next=/admin", status_code=302)
    if not user.is_admin:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "admin.html", _base_context(request, user))
