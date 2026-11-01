"""Pydantic-схемы контента: блог, FAQ, метрики, обратная связь, аналитика."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ArticleOut(BaseModel):
    """Статья блога для публичных страниц."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    title: str
    summary: str | None = None
    body_md: str
    cover_url: str | None = None
    published_at: datetime | None = None
    views: int


class ArticleListItemOut(BaseModel):
    """Карточка статьи в списке."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    title: str
    summary: str | None = None
    published_at: datetime | None = None
    views: int


class ArticleListOut(BaseModel):
    """Постраничный список статей."""

    items: list[ArticleListItemOut]
    total: int
    limit: int
    offset: int


class FaqItemOut(BaseModel):
    """Элемент FAQ."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    question: str
    answer_md: str
    category: str
    sort_order: int


class MetricReferenceOut(BaseModel):
    """Метрика в справочнике."""

    key: str
    label: str
    category: str
    category_label: str
    reference: float
    tolerance: float
    weight: float
    unit: str
    source: str
    notes: str
    reference_by_gender: dict[str, float]


class FeedbackCreate(BaseModel):
    """Отправка обращения через форму контактов."""

    subject: str = Field(min_length=3, max_length=200)
    message: str = Field(min_length=5, max_length=5000)
    name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=255)


class FeedbackCreatedOut(BaseModel):
    """Подтверждение приёма обращения."""

    id: int
    created: bool = True


class ViewPingIn(BaseModel):
    """Маячок аналитики: просмотр страницы."""

    path: str = Field(min_length=1, max_length=500)
    referer: str | None = Field(default=None, max_length=500)


class TopPageOut(BaseModel):
    """Путь и число просмотров."""

    path: str
    views: int


class ViewsByKindOut(BaseModel):
    """Тип страницы и число просмотров."""

    kind: str
    views: int


class RefererOut(BaseModel):
    """Источник переходов."""

    host: str
    views: int


class AnalyticsSummaryOut(BaseModel):
    """Сводка аналитики для админки."""

    days: int
    total_views: int
    by_kind: list[ViewsByKindOut]
    top_pages: list[TopPageOut]
    top_referers: list[RefererOut]
