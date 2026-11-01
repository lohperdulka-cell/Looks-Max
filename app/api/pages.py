"""Роутер HTML-страниц (Jinja2). Лендинг, вход, регистрация, публичный контент.

Страницы контента (блог, о метриках, о нас, приватность, условия, контакты,
помощь) собираются здесь; данные берутся из БД и ml/constants, поэтому
справочник метрик не расходится с моделью расчёта.
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
from app.services import content as content_service
from app.services import markdown_lite

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
# Фильтры шаблонов: markdown для контента, текстовое превью для meta-описаний.
templates.env.filters["markdown"] = markdown_lite.render
templates.env.filters["plain"] = markdown_lite.plain_text

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


# --- Публичные страницы контента (этап 9) ------------------------------------


def _static_context(request: Request, user: Any, **extra: Any) -> dict[str, Any]:
    """Контекст статических страниц с подвалом и текущим годом."""
    return _base_context(request, user, **extra)


@router.get("/blog", response_class=HTMLResponse)
async def blog_index(request: Request, user: OptionalUser, page: int = 1) -> HTMLResponse:
    """Список статей блога с постраничной навигацией."""
    per_page = 9
    page = max(page, 1)
    async with SessionLocal() as db:
        items, total = await content_service.list_articles(
            db, limit=per_page, offset=(page - 1) * per_page
        )
        articles = [
            {
                "slug": a.slug,
                "title": a.title,
                "summary": a.summary,
                "published_at": a.published_at or a.created_at,
                "views": a.views,
            }
            for a in items
        ]
    pages = max((total + per_page - 1) // per_page, 1)
    return templates.TemplateResponse(
        request,
        "blog.html",
        _static_context(
            request,
            user,
            articles=articles,
            page=page,
            pages=pages,
            total=total,
        ),
    )


@router.get("/blog/{slug}", response_class=HTMLResponse)
async def blog_article(request: Request, slug: str, user: OptionalUser) -> Response:
    """Страница статьи с SEO-разметкой Article."""
    import json

    async with SessionLocal() as db:
        article = await content_service.get_article_by_slug(db, slug)
        if article is None or not article.is_published:
            return templates.TemplateResponse(
                request,
                "404.html",
                _static_context(request, user),
                status_code=404,
            )
        await content_service.register_view(db, article)
        json_ld = json.dumps(content_service.article_json_ld(article), ensure_ascii=False)
        payload = {
            "slug": article.slug,
            "title": article.title,
            "summary": article.summary,
            "body_md": article.body_md,
            "published_at": article.published_at or article.created_at,
            "views": article.views,
        }
    return templates.TemplateResponse(
        request,
        "article.html",
        _static_context(request, user, article=payload, json_ld=json_ld),
    )


@router.get("/metrics", response_class=HTMLResponse)
async def metrics_page(request: Request, user: OptionalUser) -> HTMLResponse:
    """Справочник метрик: эталоны, допуски, веса и источники."""
    return templates.TemplateResponse(
        request,
        "metrics.html",
        _static_context(
            request,
            user,
            groups=content_service.metrics_by_category(),
            sources=content_service.metric_source_groups(),
            total=len(content_service.metrics_reference()),
        ),
    )


@router.get("/about", response_class=HTMLResponse)
async def about_page(request: Request, user: OptionalUser) -> HTMLResponse:
    """О платформе, команда и принципы."""
    stats = await _platform_stats()
    return templates.TemplateResponse(
        request, "about.html", _static_context(request, user, stats=stats)
    )


@router.get("/contacts", response_class=HTMLResponse)
async def contacts_page(request: Request, user: OptionalUser) -> HTMLResponse:
    """Контакты и форма обратной связи."""
    return templates.TemplateResponse(request, "contacts.html", _static_context(request, user))


@router.get("/help", response_class=HTMLResponse)
async def help_page(request: Request, user: OptionalUser) -> HTMLResponse:
    """Помощь: FAQ из БД и базовые подсказки."""
    async with SessionLocal() as db:
        items = await content_service.list_faq(db)
    faq = [
        {"question": item.question, "answer_md": item.answer_md, "category": item.category}
        for item in items
    ]
    return templates.TemplateResponse(request, "help.html", _static_context(request, user, faq=faq))


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request, user: OptionalUser) -> HTMLResponse:
    """Политика конфиденциальности."""
    return templates.TemplateResponse(request, "privacy.html", _static_context(request, user))


@router.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request, user: OptionalUser) -> HTMLResponse:
    """Пользовательское соглашение."""
    return templates.TemplateResponse(request, "terms.html", _static_context(request, user))
