"""Роутер контента: блог, FAQ, справочник метрик, обратная связь, аналитика, SEO."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, Response

from app.core.deps import CurrentAdmin, DbSession, OptionalUser
from app.core.limiter import limiter
from app.schemas.content import (
    AnalyticsSummaryOut,
    ArticleListItemOut,
    ArticleListOut,
    ArticleOut,
    FaqItemOut,
    FeedbackCreate,
    FeedbackCreatedOut,
    MetricReferenceOut,
    RefererOut,
    TopPageOut,
    ViewPingIn,
    ViewsByKindOut,
)
from app.services import analytics as analytics_service
from app.services import content as content_service

router = APIRouter(tags=["content"])


@router.get("/api/articles", response_model=ArticleListOut)
async def list_articles(
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ArticleListOut:
    """Опубликованные статьи блога."""
    items, total = await content_service.list_articles(db, limit=limit, offset=offset)
    return ArticleListOut(
        items=[ArticleListItemOut.model_validate(a) for a in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/api/articles/{slug}", response_model=ArticleOut)
async def get_article(slug: str, db: DbSession) -> ArticleOut:
    """Статья по slug (только опубликованная)."""
    article = await content_service.get_article_by_slug(db, slug)
    if article is None or not article.is_published:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Статья не найдена")
    await content_service.register_view(db, article)
    return ArticleOut.model_validate(article)


@router.get("/api/faq", response_model=list[FaqItemOut])
async def faq(db: DbSession) -> list[FaqItemOut]:
    """Активные элементы FAQ."""
    items = await content_service.list_faq(db)
    return [FaqItemOut.model_validate(item) for item in items]


@router.get("/api/metrics/reference", response_model=list[MetricReferenceOut])
async def metrics_reference() -> list[MetricReferenceOut]:
    """Справочник метрик: эталоны, допуски, веса и источники."""
    return [MetricReferenceOut(**item) for item in content_service.metrics_reference()]


@router.post(
    "/api/feedback", response_model=FeedbackCreatedOut, status_code=status.HTTP_201_CREATED
)
@limiter.limit("5/minute")
async def create_feedback(
    request: Request, payload: FeedbackCreate, user: OptionalUser, db: DbSession
) -> FeedbackCreatedOut:
    """Принимает обращение из формы обратной связи."""
    from datetime import UTC, datetime

    from app.models.content import Feedback

    entry = Feedback(
        user_id=user.id if user else None,
        name=payload.name,
        email=payload.email,
        subject=payload.subject,
        message=payload.message,
        is_processed=False,
        created_at=datetime.now(UTC),
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return FeedbackCreatedOut(id=entry.id)


@router.post("/api/analytics/ping", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("120/minute")
async def ping(request: Request, payload: ViewPingIn, user: OptionalUser, db: DbSession) -> None:
    """Маячок аналитики: фиксирует просмотр публичной страницы."""
    await analytics_service.record_view(
        db,
        path=payload.path,
        user_id=user.id if user else None,
        referer=payload.referer or request.headers.get("referer"),
    )


@router.get("/api/admin/analytics", response_model=AnalyticsSummaryOut)
async def analytics_summary(
    admin: CurrentAdmin,
    db: DbSession,
    days: int = Query(default=30, ge=1, le=365),
) -> AnalyticsSummaryOut:
    """Сводка аналитики просмотров для админки."""
    return AnalyticsSummaryOut(
        days=days,
        total_views=await analytics_service.views_total(db, days),
        by_kind=[ViewsByKindOut(**row) for row in await analytics_service.views_by_kind(db, days)],
        top_pages=[TopPageOut(**row) for row in await analytics_service.top_pages(db, days)],
        top_referers=[RefererOut(**row) for row in await analytics_service.top_referers(db, days)],
    )


@router.get("/sitemap.xml", response_class=Response)
async def sitemap(db: DbSession) -> Response:
    """Карта сайта: статические страницы и опубликованные статьи."""
    articles, _ = await content_service.list_articles(db, limit=500)
    xml = content_service.sitemap_xml(articles)
    return Response(content=xml, media_type="application/xml")


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> PlainTextResponse:
    """robots.txt с указанием sitemap."""
    return PlainTextResponse(content_service.robots_txt())
