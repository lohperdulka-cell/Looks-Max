"""Pydantic-схемы фото, landmarks и предпросмотра оценки."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LandmarksPayload(BaseModel):
    """Landmark-точки: нормализованные координаты 0..1 (x, y)."""

    points: list[tuple[float, float]] = Field(min_length=5)
    provider: str = "manual"
    bbox: tuple[float, float, float, float] | None = None


class PhotoOut(BaseModel):
    """Метаданные фото."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    content_type: str
    size_bytes: int
    width: int | None = None
    height: int | None = None
    landmarks_provider: str | None = None
    is_aligned: bool
    quality_score: float | None = None
    is_public_in_battles: bool
    created_at: datetime


class PhotoDetail(PhotoOut):
    """Фото с точками и предупреждениями качества."""

    landmarks: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    image_url: str


class MetricPreview(BaseModel):
    """Одна метрика в предпросмотре."""

    key: str
    label: str
    category: str
    raw_value: float | None = None
    reference_value: float | None = None
    deviation: float | None = None
    score: float
    weight: float
    unit: str = ""
    source: str = ""
    notes: str = ""


class PreviewOut(BaseModel):
    """Предпросмотр оценки без сохранения отчёта."""

    overall: float
    harmony: float
    categories: dict[str, float]
    metrics: list[MetricPreview]
    strengths: list[MetricPreview]
    growth: list[MetricPreview]
    missing: list[str] = Field(default_factory=list)
    disclaimer: str


class PhotoVisibilityUpdate(BaseModel):
    """Изменение видимости фото (opt-in для батлов, скрытие)."""

    is_hidden: bool | None = None
    is_public_in_battles: bool | None = None


class PhotoListOut(BaseModel):
    """Список фото с общим количеством."""

    items: list[PhotoOut]
    total: int


class UploadKind(BaseModel):
    """Параметр типа загрузки (для будущих альбомов)."""

    kind: Literal["frontal", "profile", "other"] = "frontal"
