"""Сервис контента: блог, FAQ, справочник метрик, SEO-файлы, аналитика.

Контент читается из БД (наполняется через админку). Справочник метрик строится
из app.ml.constants — это единственный источник правды о весах и эталонах,
поэтому страница «О метриках» не расходится с моделью.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.ml.constants import CATEGORY_LABELS, METRIC_SPECS, MetricSpec
from app.models.content import Article, FaqItem


async def list_articles(
    db: AsyncSession, limit: int = 20, offset: int = 0, include_drafts: bool = False
) -> tuple[list[Article], int]:
    """Опубликованные статьи блога (свежие сверху) и их общее число."""
    stmt = select(Article)
    count_stmt = select(func.count()).select_from(Article)
    if not include_drafts:
        stmt = stmt.where(Article.is_published.is_(True))
        count_stmt = count_stmt.where(Article.is_published.is_(True))

    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        (
            await db.execute(
                stmt.order_by(Article.published_at.desc().nullslast(), Article.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def get_article_by_slug(db: AsyncSession, slug: str) -> Article | None:
    """Статья по slug."""
    return (await db.execute(select(Article).where(Article.slug == slug))).scalar_one_or_none()


async def register_view(db: AsyncSession, article: Article) -> None:
    """Инкрементирует счётчик просмотров статьи."""
    article.views += 1
    await db.commit()


async def list_faq(db: AsyncSession, active_only: bool = True) -> list[FaqItem]:
    """Элементы FAQ, сгруппированные по порядку сортировки."""
    stmt = select(FaqItem)
    if active_only:
        stmt = stmt.where(FaqItem.is_active.is_(True))
    return list((await db.execute(stmt.order_by(FaqItem.sort_order, FaqItem.id))).scalars().all())


def group_faq(items: list[FaqItem]) -> dict[str, list[FaqItem]]:
    """Группирует FAQ по категориям, сохраняя порядок появления."""
    grouped: dict[str, list[FaqItem]] = {}
    for item in items:
        grouped.setdefault(item.category, []).append(item)
    return grouped


def metrics_reference() -> list[dict]:
    """Справочник метрик для страницы «О метриках» и API.

    Каждая запись содержит эталон, допуск, вес, единицу и источник — данные
    берутся из ml/constants.py, а не дублируются в шаблоне.
    """
    reference: list[dict] = []
    for spec in METRIC_SPECS:
        reference.append(
            {
                "key": spec.key,
                "label": spec.label,
                "category": str(spec.category),
                "category_label": CATEGORY_LABELS.get(spec.category, str(spec.category)),
                "reference": spec.reference,
                "tolerance": spec.tolerance,
                "weight": spec.weight,
                "unit": spec.unit,
                "source": spec.source,
                "notes": spec.notes,
                "reference_by_gender": dict(spec.reference_by_gender),
            }
        )
    return reference


def metrics_by_category() -> dict[str, dict]:
    """Справочник метрик, сгруппированный по категориям."""
    grouped: dict[str, dict] = {}
    for item in metrics_reference():
        bucket = grouped.setdefault(
            item["category"],
            {"label": item["category_label"], "items": []},
        )
        bucket["items"].append(item)
    return grouped


def _absolute(path: str) -> str:
    """Абсолютный URL по пути с учётом base_url."""
    return f"{settings.base_url.rstrip('/')}/{path.lstrip('/')}"


def sitemap_xml(articles: list[Article]) -> str:
    """Генерирует sitemap.xml: статические страницы + опубликованные статьи."""
    static_pages = [
        ("/", "1.0", "weekly"),
        ("/metrics", "0.8", "monthly"),
        ("/blog", "0.8", "weekly"),
        ("/about", "0.6", "monthly"),
        ("/contacts", "0.5", "monthly"),
        ("/privacy", "0.3", "yearly"),
        ("/terms", "0.3", "yearly"),
    ]
    today = datetime.now(UTC).date().isoformat()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path, priority, freq in static_pages:
        lines.extend(
            [
                "  <url>",
                f"    <loc>{_absolute(path)}</loc>",
                f"    <lastmod>{today}</lastmod>",
                f"    <changefreq>{freq}</changefreq>",
                f"    <priority>{priority}</priority>",
                "  </url>",
            ]
        )
    for article in articles:
        lastmod = (article.published_at or article.created_at).date().isoformat()
        lines.extend(
            [
                "  <url>",
                f"    <loc>{_absolute(f'/blog/{article.slug}')}</loc>",
                f"    <lastmod>{lastmod}</lastmod>",
                "    <changefreq>monthly</changefreq>",
                "    <priority>0.6</priority>",
                "  </url>",
            ]
        )
    lines.append("</urlset>")
    return "\n".join(lines)


def robots_txt() -> str:
    """robots.txt: закрывает служебные разделы и указывает sitemap."""
    disallow = ["/api/", "/admin", "/profile", "/report", "/r/", "/chat", "/exercises"]
    lines = [
        "User-agent: *",
        "Allow: /",
        *[f"Disallow: {path}" for path in disallow],
        "",
        f"Sitemap: {_absolute('/sitemap.xml')}",
    ]
    return "\n".join(lines)


def article_json_ld(article: Article) -> dict:
    """Schema.org Article для SEO-разметки."""
    return {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": article.title,
        "description": article.summary or "",
        "datePublished": (article.published_at or article.created_at).isoformat(),
        "author": {"@type": "Organization", "name": settings.app_name},
        "mainEntityOfPage": _absolute(f"/blog/{article.slug}"),
    }


def metric_source_groups() -> list[dict]:
    """Уникальные источники метрик со списком метрик каждого источника."""
    by_source: dict[str, list[MetricSpec]] = {}
    for spec in METRIC_SPECS:
        by_source.setdefault(spec.source, []).append(spec)
    return [
        {"source": source, "metrics": [s.label for s in specs]}
        for source, specs in by_source.items()
        if source
    ]
