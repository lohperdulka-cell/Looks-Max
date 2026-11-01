"""Роутер админки: дашборд, пользователи, модерация, веса, логи, фидбек, контент."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from slugify import slugify
from sqlalchemy import select

from app.core.deps import CurrentAdmin, DbSession
from app.models.content import Article, FaqItem, Feedback
from app.models.photo import Photo
from app.models.user import User
from app.schemas.admin import (
    ActivityPointOut,
    AdminLogOut,
    AdminUserListOut,
    AdminUserOut,
    AdminUserUpdate,
    ArticleAdminOut,
    ArticleUpsert,
    DashboardOut,
    ErrorLogOut,
    FaqAdminOut,
    FaqUpsert,
    FeedbackOut,
    MetricWeightOut,
    MetricWeightUpsert,
    PhotoModerationListOut,
    PhotoModerationOut,
)
from app.services import admin as admin_service

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(admin: CurrentAdmin, db: DbSession) -> DashboardOut:
    """Сводная статистика платформы."""
    stats = await admin_service.dashboard(db)
    return DashboardOut(
        users_total=stats.users_total,
        users_new_7d=stats.users_new_7d,
        users_banned=stats.users_banned,
        photos_total=stats.photos_total,
        reports_total=stats.reports_total,
        reports_7d=stats.reports_7d,
        battles_total=stats.battles_total,
        votes_total=stats.votes_total,
        exercises_done=stats.exercises_done,
        feedback_unread=stats.feedback_unread,
        errors_24h=stats.errors_24h,
    )


@router.get("/activity", response_model=list[ActivityPointOut])
async def activity(
    admin: CurrentAdmin, db: DbSession, days: int = Query(default=14, ge=7, le=90)
) -> list[ActivityPointOut]:
    """Регистрации и отчёты по дням."""
    return [ActivityPointOut(**row) for row in await admin_service.daily_activity(db, days)]


@router.get("/users", response_model=AdminUserListOut)
async def users(
    admin: CurrentAdmin,
    db: DbSession,
    query: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AdminUserListOut:
    """Список пользователей с поиском."""
    items, total = await admin_service.list_users(db, query=query, limit=limit, offset=offset)
    return AdminUserListOut(
        items=[AdminUserOut.model_validate(u) for u in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: int, payload: AdminUserUpdate, admin: CurrentAdmin, db: DbSession
) -> AdminUserOut:
    """Меняет флаги пользователя (бан, активность, роль) и пишет в журнал."""
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    if target.id == admin.id and payload.is_admin is False:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Нельзя снять права с себя"
        )

    changes: dict[str, object] = {}
    for field in ("is_active", "is_admin", "is_banned"):
        value = getattr(payload, field)
        if value is not None:
            setattr(target, field, value)
            changes[field] = value

    if changes:
        await db.commit()
        await db.refresh(target)
        details = dict(changes)
        if payload.ban_reason:
            details["ban_reason"] = payload.ban_reason
        await admin_service.log_action(db, admin, "user.update", "user", target.id, details)
    return AdminUserOut.model_validate(target)


@router.get("/photos", response_model=PhotoModerationListOut)
async def photos(
    admin: CurrentAdmin,
    db: DbSession,
    only_public: bool = Query(default=False),
    limit: int = Query(default=60, ge=1, le=200),
) -> PhotoModerationListOut:
    """Очередь фото для модерации."""
    stmt = select(Photo, User.username).join(User, User.id == Photo.user_id)
    if only_public:
        stmt = stmt.where(Photo.is_public_in_battles.is_(True))
    rows = (await db.execute(stmt.order_by(Photo.created_at.desc()).limit(limit))).all()

    items = [
        PhotoModerationOut(
            id=photo.id,
            user_id=photo.user_id,
            username=username,
            image_url=f"/api/photos/{photo.id}/image",
            is_hidden=photo.is_hidden,
            is_public_in_battles=photo.is_public_in_battles,
            quality_score=photo.quality_score,
            created_at=photo.created_at,
        )
        for photo, username in rows
    ]
    return PhotoModerationListOut(items=items, total=len(items))


@router.post("/photos/{photo_id}/hide", response_model=PhotoModerationOut)
async def hide_photo(
    photo_id: int, admin: CurrentAdmin, db: DbSession, hidden: bool = Query(default=True)
) -> PhotoModerationOut:
    """Скрывает или возвращает фото и пишет действие в журнал."""
    photo = await db.get(Photo, photo_id)
    if photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Фото не найдено")

    photo.is_hidden = hidden
    if hidden:
        photo.is_public_in_battles = False
    await db.commit()
    await db.refresh(photo)

    owner = await db.get(User, photo.user_id)
    await admin_service.log_action(
        db, admin, "photo.hide" if hidden else "photo.unhide", "photo", photo.id
    )
    return PhotoModerationOut(
        id=photo.id,
        user_id=photo.user_id,
        username=owner.username if owner else "",
        image_url=f"/api/photos/{photo.id}/image",
        is_hidden=photo.is_hidden,
        is_public_in_battles=photo.is_public_in_battles,
        quality_score=photo.quality_score,
        created_at=photo.created_at,
    )


@router.get("/metric-weights", response_model=list[MetricWeightOut])
async def metric_weights(admin: CurrentAdmin, db: DbSession) -> list[MetricWeightOut]:
    """Переопределённые веса метрик."""
    rows = await admin_service.list_metric_weights(db)
    return [MetricWeightOut.model_validate(row) for row in rows]


@router.put("/metric-weights", response_model=MetricWeightOut)
async def upsert_metric_weight(
    payload: MetricWeightUpsert, admin: CurrentAdmin, db: DbSession
) -> MetricWeightOut:
    """Создаёт или обновляет вес метрики."""
    from app.ml.constants import METRIC_BY_KEY

    if payload.key not in METRIC_BY_KEY:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Неизвестная метрика: {payload.key}",
        )

    row = await admin_service.upsert_metric_weight(
        db,
        key=payload.key,
        weight=payload.weight,
        reference_value=payload.reference_value,
        is_enabled=payload.is_enabled,
        note=payload.note,
    )
    await admin_service.log_action(
        db,
        admin,
        "metric_weight.upsert",
        "metric_weight",
        row.id,
        {"key": payload.key, "weight": payload.weight},
    )
    return MetricWeightOut.model_validate(row)


@router.delete("/metric-weights/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_metric_weight(key: str, admin: CurrentAdmin, db: DbSession) -> None:
    """Удаляет переопределение веса (возврат к значению из кода)."""
    if not await admin_service.delete_metric_weight(db, key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Переопределение не найдено"
        )
    await admin_service.log_action(
        db, admin, "metric_weight.delete", "metric_weight", None, {"key": key}
    )


@router.get("/logs", response_model=list[AdminLogOut])
async def logs(
    admin: CurrentAdmin, db: DbSession, limit: int = Query(default=100, ge=1, le=500)
) -> list[AdminLogOut]:
    """Журнал действий администраторов."""
    return [
        AdminLogOut.model_validate(row) for row in await admin_service.recent_admin_logs(db, limit)
    ]


@router.get("/errors", response_model=list[ErrorLogOut])
async def errors(
    admin: CurrentAdmin, db: DbSession, limit: int = Query(default=100, ge=1, le=500)
) -> list[ErrorLogOut]:
    """Последние ошибки приложения."""
    return [ErrorLogOut.model_validate(row) for row in await admin_service.recent_errors(db, limit)]


@router.get("/feedback", response_model=list[FeedbackOut])
async def feedback(
    admin: CurrentAdmin,
    db: DbSession,
    only_unread: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[FeedbackOut]:
    """Обращения обратной связи."""
    rows = await admin_service.list_feedback(db, only_unread=only_unread, limit=limit)
    return [FeedbackOut.model_validate(row) for row in rows]


@router.post("/feedback/{feedback_id}/process", response_model=FeedbackOut)
async def process_feedback(feedback_id: int, admin: CurrentAdmin, db: DbSession) -> FeedbackOut:
    """Помечает обращение обработанным."""
    item = await db.get(Feedback, feedback_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Обращение не найдено")
    await admin_service.mark_feedback_processed(db, item)
    await admin_service.log_action(db, admin, "feedback.process", "feedback", item.id)
    return FeedbackOut.model_validate(item)


def _base_slug(title: str, desired: str | None) -> str:
    """Готовит базовый slug; уникальность обеспечивается в create_article."""
    return slugify(desired or title, max_length=180) or "article"


@router.get("/articles", response_model=list[ArticleAdminOut])
async def articles(admin: CurrentAdmin, db: DbSession) -> list[ArticleAdminOut]:
    """Все статьи, включая черновики."""
    return [ArticleAdminOut.model_validate(row) for row in await admin_service.list_articles(db)]


@router.post("/articles", response_model=ArticleAdminOut, status_code=status.HTTP_201_CREATED)
async def create_article(
    payload: ArticleUpsert, admin: CurrentAdmin, db: DbSession
) -> ArticleAdminOut:
    """Создаёт статью блога с уникальным slug."""
    base = _base_slug(payload.title, payload.slug)
    slug = base
    suffix = 2
    while (
        await db.execute(select(Article.id).where(Article.slug == slug))
    ).scalar_one_or_none() is not None:
        slug = f"{base}-{suffix}"
        suffix += 1

    article = Article(
        slug=slug,
        title=payload.title,
        summary=payload.summary,
        body_md=payload.body_md,
        is_published=payload.is_published,
        published_at=datetime.now(UTC) if payload.is_published else None,
    )
    db.add(article)
    await db.commit()
    await db.refresh(article)
    await admin_service.log_action(db, admin, "article.create", "article", article.id)
    return ArticleAdminOut.model_validate(article)


@router.patch("/articles/{article_id}", response_model=ArticleAdminOut)
async def update_article(
    article_id: int, payload: ArticleUpsert, admin: CurrentAdmin, db: DbSession
) -> ArticleAdminOut:
    """Обновляет статью блога."""
    article = await db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Статья не найдена")

    was_published = article.is_published
    article.title = payload.title
    article.summary = payload.summary
    article.body_md = payload.body_md
    article.is_published = payload.is_published
    if payload.is_published and not was_published:
        article.published_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(article)
    await admin_service.log_action(db, admin, "article.update", "article", article.id)
    return ArticleAdminOut.model_validate(article)


@router.delete("/articles/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_article(article_id: int, admin: CurrentAdmin, db: DbSession) -> None:
    """Удаляет статью."""
    article = await db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Статья не найдена")
    await db.delete(article)
    await db.commit()
    await admin_service.log_action(db, admin, "article.delete", "article", article_id)


@router.get("/faq", response_model=list[FaqAdminOut])
async def faq(admin: CurrentAdmin, db: DbSession) -> list[FaqAdminOut]:
    """Элементы FAQ."""
    return [FaqAdminOut.model_validate(row) for row in await admin_service.list_faq(db)]


@router.post("/faq", response_model=FaqAdminOut, status_code=status.HTTP_201_CREATED)
async def create_faq(payload: FaqUpsert, admin: CurrentAdmin, db: DbSession) -> FaqAdminOut:
    """Добавляет элемент FAQ."""
    item = FaqItem(
        question=payload.question,
        answer_md=payload.answer_md,
        category=payload.category,
        sort_order=payload.sort_order,
        is_active=payload.is_active,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    await admin_service.log_action(db, admin, "faq.create", "faq", item.id)
    return FaqAdminOut.model_validate(item)


@router.patch("/faq/{item_id}", response_model=FaqAdminOut)
async def update_faq(
    item_id: int, payload: FaqUpsert, admin: CurrentAdmin, db: DbSession
) -> FaqAdminOut:
    """Обновляет элемент FAQ."""
    item = await db.get(FaqItem, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Элемент не найден")

    item.question = payload.question
    item.answer_md = payload.answer_md
    item.category = payload.category
    item.sort_order = payload.sort_order
    item.is_active = payload.is_active
    await db.commit()
    await db.refresh(item)
    await admin_service.log_action(db, admin, "faq.update", "faq", item.id)
    return FaqAdminOut.model_validate(item)


@router.delete("/faq/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_faq(item_id: int, admin: CurrentAdmin, db: DbSession) -> None:
    """Удаляет элемент FAQ."""
    item = await db.get(FaqItem, item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Элемент не найден")
    await db.delete(item)
    await db.commit()
    await admin_service.log_action(db, admin, "faq.delete", "faq", item_id)
