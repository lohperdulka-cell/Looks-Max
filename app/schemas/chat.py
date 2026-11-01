"""Pydantic-схемы ИИ-чата и looksmax-планов."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatMessageOut(BaseModel):
    """Сообщение чата."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    created_at: datetime


class ChatSessionOut(BaseModel):
    """Сессия чата с историей."""

    id: int
    title: str
    report_id: int | None = None
    created_at: datetime
    messages: list[ChatMessageOut] = Field(default_factory=list)


class ChatRequest(BaseModel):
    """Вопрос пользователя к ИИ-консультанту."""

    message: str = Field(min_length=1, max_length=2000)
    session_id: int | None = None


class QuickPromptOut(BaseModel):
    """Быстрый промпт."""

    title: str
    prompt: str


class ChatMetaOut(BaseModel):
    """Метаданные чата: доступность LLM и быстрые промпты."""

    llm_enabled: bool
    quick_prompts: list[QuickPromptOut]


class PlanRequest(BaseModel):
    """Запрос на генерацию looksmax-плана."""

    horizon_days: int = Field(default=30, ge=7, le=180)


class PlanOut(BaseModel):
    """Сохранённый looksmax-план."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    horizon_days: int
    content_md: str
    model: str | None = None
    report_id: int | None = None
    generated_at: datetime


class PlanListOut(BaseModel):
    """Список планов."""

    items: list[PlanOut]
    total: int
