"""Pydantic-схемы отчётов об оценке."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MetricOut(BaseModel):
    """Метрика в отчёте."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    category: str
    category_label: str
    raw_value: float | None = None
    reference_value: float | None = None
    deviation: float | None = None
    score: float
    weight: float
    unit: str = ""
    source: str = ""
    notes: str = ""


class RecommendationOut(BaseModel):
    """Рекомендация по метрике."""

    key: str
    label: str
    category: str
    priority: int
    severity: str
    direction: str
    text: str
    actions: list[str] = Field(default_factory=list)
    source: str = ""


class CategoryOut(BaseModel):
    """Агрегат по категории."""

    key: str
    label: str
    score: float
    weight: float
    metrics_count: int


class ReportSummary(BaseModel):
    """Краткая карточка отчёта для списков и истории."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    photo_id: int
    overall_score: float
    harmony_score: float | None = None
    model_version: str
    is_public: bool
    public_token: str | None = None
    created_at: datetime


class ReportOut(ReportSummary):
    """Полный отчёт с метриками, категориями и рекомендациями."""

    categories_detail: list[CategoryOut] = Field(default_factory=list)
    metrics: list[MetricOut] = Field(default_factory=list)
    recommendations: list[RecommendationOut] = Field(default_factory=list)
    strengths: list[MetricOut] = Field(default_factory=list)
    growth: list[MetricOut] = Field(default_factory=list)
    disclaimer: str = ""
    image_url: str = ""
    is_demo: bool = False


class ReportListOut(BaseModel):
    """История отчётов."""

    items: list[ReportSummary]
    total: int


class PublicReportOut(BaseModel):
    """Публичный отчёт по анонимной ссылке (без персональных данных)."""

    overall_score: float
    harmony_score: float | None = None
    categories_detail: list[CategoryOut] = Field(default_factory=list)
    metrics: list[MetricOut] = Field(default_factory=list)
    recommendations: list[RecommendationOut] = Field(default_factory=list)
    strengths: list[MetricOut] = Field(default_factory=list)
    growth: list[MetricOut] = Field(default_factory=list)
    disclaimer: str = ""
    model_version: str
    created_at: datetime
    is_demo: bool = False


class ShareUpdate(BaseModel):
    """Включение/выключение публичной ссылки."""

    is_public: bool
