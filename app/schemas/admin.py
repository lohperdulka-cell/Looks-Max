"""Pydantic-схемы админки."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DashboardOut(BaseModel):
    """Сводка платформы для дашборда."""

    users_total: int
    users_new_7d: int
    users_banned: int
    photos_total: int
    reports_total: int
    reports_7d: int
    battles_total: int
    votes_total: int
    exercises_done: int
    feedback_unread: int
    errors_24h: int


class ActivityPointOut(BaseModel):
    """Точка графика активности."""

    day: str
    users: int
    reports: int


class AdminUserOut(BaseModel):
    """Пользователь в списке админки."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str | None = None
    display_name: str | None = None
    is_active: bool
    is_admin: bool
    is_banned: bool
    elo: int
    streak_days: int
    created_at: datetime


class AdminUserListOut(BaseModel):
    """Страница списка пользователей."""

    items: list[AdminUserOut]
    total: int
    limit: int
    offset: int


class AdminUserUpdate(BaseModel):
    """Изменение флагов пользователя из админки."""

    is_active: bool | None = None
    is_admin: bool | None = None
    is_banned: bool | None = None
    ban_reason: str | None = Field(default=None, max_length=500)


class PhotoModerationOut(BaseModel):
    """Фото в очереди модерации."""

    id: int
    user_id: int
    username: str
    image_url: str
    is_hidden: bool
    is_public_in_battles: bool
    quality_score: float | None = None
    created_at: datetime


class PhotoModerationListOut(BaseModel):
    """Список фото для модерации."""

    items: list[PhotoModerationOut]
    total: int


class MetricWeightOut(BaseModel):
    """Переопределение веса метрики."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    weight: float
    reference_value: float | None = None
    is_enabled: bool
    note: str | None = None
    updated_at: datetime | None = None


class MetricWeightUpsert(BaseModel):
    """Создание/обновление веса метрики."""

    key: str = Field(min_length=2, max_length=64)
    weight: float = Field(ge=0.0, le=10.0)
    reference_value: float | None = None
    is_enabled: bool = True
    note: str | None = Field(default=None, max_length=500)


class AdminLogOut(BaseModel):
    """Запись журнала действий."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    admin_id: int | None = None
    action: str
    target_type: str | None = None
    target_id: int | None = None
    details: dict | None = None
    created_at: datetime


class ErrorLogOut(BaseModel):
    """Запись об ошибке."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    level: str
    path: str | None = None
    message: str
    traceback: str | None = None
    created_at: datetime


class FeedbackOut(BaseModel):
    """Обращение обратной связи."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None = None
    name: str | None = None
    email: str | None = None
    subject: str
    message: str
    is_processed: bool
    processed_at: datetime | None = None
    created_at: datetime


class ArticleAdminOut(BaseModel):
    """Статья блога для админки."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    title: str
    summary: str | None = None
    body_md: str
    is_published: bool
    views: int
    created_at: datetime


class ArticleUpsert(BaseModel):
    """Создание/обновление статьи."""

    title: str = Field(min_length=3, max_length=240)
    summary: str | None = Field(default=None, max_length=1000)
    body_md: str = Field(min_length=1)
    is_published: bool = True
    slug: str | None = Field(default=None, max_length=200)


class FaqAdminOut(BaseModel):
    """Элемент FAQ для админки."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    question: str
    answer_md: str
    category: str
    sort_order: int
    is_active: bool


class FaqUpsert(BaseModel):
    """Создание/обновление элемента FAQ."""

    question: str = Field(min_length=3, max_length=300)
    answer_md: str = Field(min_length=1)
    category: str = Field(default="general", max_length=48)
    sort_order: int = 0
    is_active: bool = True
