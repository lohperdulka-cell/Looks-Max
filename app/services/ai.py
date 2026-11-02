"""ИИ-консультант: контекст, стриминг ответа, looksmax-план.

Провайдер LLM опционален (settings.llm_enabled). Когда он выключен или
недоступен, работает детерминированный оффлайн-советник: ответ собирается из
тех же данных (метрики отчёта, зоны роста, рекомендации, упражнения), поэтому
чат остаётся рабочим без внешних ключей и без сети.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.logging_config import get_logger
from app.models.chat import ChatMessage, ChatSession, LooksmaxPlan
from app.models.report import Report
from app.models.user import User
from app.services import exercises as exercises_service

logger = get_logger(__name__)

SYSTEM_PROMPT = (
    "Ты — ИИ-консультант платформы FaceMetric. Помогаешь улучшить внешность "
    "неинвазивными методами: уход, сон, осанка, мимические упражнения, свет и "
    "ракурс на фото. Опирайся на метрики лица пользователя и давай конкретные, "
    "проверяемые шаги. Не давай медицинских советов, не рекомендуй операции и "
    "препараты. Будь честен: модель лишь формализует пропорции, а не истину о "
    "внешности."
)

QUICK_PROMPTS: tuple[dict[str, str], ...] = (
    {
        "title": "Разбор отчёта",
        "prompt": "Объясни мой последний отчёт простыми словами: что уже хорошо, а что тянет вниз.",
    },
    {
        "title": "План на 30 дней",
        "prompt": "Составь план на 30 дней по моим слабым метрикам, разбитый по неделям.",
    },
    {
        "title": "Линия челюсти",
        "prompt": "Что можно сделать для линии челюсти без операций и процедур?",
    },
    {
        "title": "Фото для оценки",
        "prompt": "Как правильно фотографироваться, чтобы оценка была честной?",
    },
    {
        "title": "Уход за кожей",
        "prompt": "Дай базовую рутину ухода за кожей лица утром и вечером.",
    },
)


@dataclass(slots=True)
class ReportContext:
    """Сжатый контекст отчёта для промпта и оффлайн-ответа."""

    report_id: int | None = None
    overall: float = 0.0
    harmony: float = 0.0
    strengths: list[dict] = field(default_factory=list)
    growth: list[dict] = field(default_factory=list)
    recommendations: list[dict] = field(default_factory=list)

    def as_prompt_block(self) -> str:
        """Текстовый блок контекста для LLM."""
        if self.report_id is None:
            return "У пользователя ещё нет сохранённого отчёта."

        lines = [
            f"Последний отчёт #{self.report_id}: общий балл {self.overall:.1f}/10, "
            f"гармония {self.harmony:.1f}/10.",
            "Сильные стороны: "
            + ", ".join(f"{m['label']} {m['score']:.1f}" for m in self.strengths[:5]),
            "Зоны роста: " + ", ".join(f"{m['label']} {m['score']:.1f}" for m in self.growth[:5]),
        ]
        if self.recommendations:
            lines.append("Рекомендации модели:")
            lines.extend(f"  - {rec['label']}: {rec['text']}" for rec in self.recommendations[:5])
        return "\n".join(lines)


async def load_report_context(db: AsyncSession, user_id: int) -> ReportContext:
    """Берёт последний отчёт пользователя и сжимает его в контекст.

    Метрики и фото подгружаются сразу (selectinload): serialize_report читает
    report.metrics и report.photo, а в async-сессии ленивая загрузка связи
    падает с MissingGreenlet.
    """
    report = (
        await db.execute(
            select(Report)
            .where(Report.user_id == user_id)
            .order_by(Report.created_at.desc())
            .limit(1)
            .options(selectinload(Report.metrics), selectinload(Report.photo))
        )
    ).scalar_one_or_none()
    if report is None:
        return ReportContext()

    from app.services.reports import serialize_report

    payload = serialize_report(report)
    return ReportContext(
        report_id=payload.id,
        overall=payload.overall_score,
        harmony=payload.harmony_score or 0.0,
        strengths=[m.model_dump() for m in payload.strengths],
        growth=[m.model_dump() for m in payload.growth],
        recommendations=[r.model_dump() for r in payload.recommendations],
    )


def _plan_block(context: ReportContext, exercises: list) -> str:
    """Собирает план на 30 дней по зонам роста."""
    weeks = [
        "Неделя 1 — привычки: сон 7–9 часов, вода, базовая рутина ухода, осанка.",
        "Неделя 2 — нагрузка: упражнения на зоны роста каждый день, контроль фото.",
        "Неделя 3 — техника: съёмка по правилам (анфас, ровный свет, камера на уровне глаз).",
        "Неделя 4 — контроль: новый отчёт и сравнение «до/после» в профиле.",
    ]
    growth = ", ".join(m["label"] for m in context.growth[:3]) or "текущие метрики"
    block = [f"План на 30 дней (фокус: {growth}):", *[f"- {w}" for w in weeks]]
    if exercises:
        block.append(
            "Упражнения из библиотеки: " + ", ".join(ex.title for ex in exercises[:4]) + "."
        )
    return "\n".join(block)


def build_offline_answer(question: str, context: ReportContext, exercises: list) -> str:
    """Детерминированный ответ без LLM: собирается из отчёта и упражнений.

    Это не заглушка: ответ строится по реальным метрикам пользователя, называет
    зоны роста и предлагает конкретные упражнения из библиотеки.
    """
    parts: list[str] = []
    lowered = question.lower()

    if context.report_id is None:
        parts.append(
            "У вас пока нет сохранённого отчёта. Загрузите фото на странице оценки — "
            "и я разберу метрики по пунктам."
        )
    else:
        parts.append(
            f"По последнему отчёту общий балл — **{context.overall:.1f}/10**, "
            f"гармония (согласованность метрик) — **{context.harmony:.1f}/10**."
        )
        if context.strengths:
            best = ", ".join(f"{m['label']} ({m['score']:.1f})" for m in context.strengths[:3])
            parts.append(f"Сильные стороны: {best}.")
        if context.growth:
            weak = ", ".join(f"{m['label']} ({m['score']:.1f})" for m in context.growth[:3])
            parts.append(f"Что тянет балл вниз: {weak}.")

    if "план" in lowered or "30" in lowered or "недел" in lowered:
        parts.append(_plan_block(context, exercises))
    elif context.recommendations:
        parts.append("Что делать по шагам:")
        for rec in context.recommendations[:3]:
            actions = "; ".join(rec.get("actions", [])[:2])
            parts.append(f"- **{rec['label']}**: {rec['text']} {actions}")

    if exercises:
        titles = ", ".join(ex.title for ex in exercises[:3])
        parts.append(f"Подходящие упражнения: {titles}.")

    parts.append(
        "Если нужен другой акцент — спросите точнее, например: «план на 30 дней», "
        "«линия челюсти», «уход за кожей»."
    )
    return "\n\n".join(parts)


def _history_for_llm(messages: list[ChatMessage]) -> list[dict[str, str]]:
    """Готовит историю диалога для LLM (последние 12 сообщений)."""
    tail = messages[-12:]
    return [{"role": m.role, "content": m.content} for m in tail if m.role in {"user", "assistant"}]


async def _stream_llm(
    db: AsyncSession, user: User, session: ChatSession, question: str
) -> AsyncIterator[str]:
    """Стримит ответ от OpenAI-совместимого провайдера (SSE delta)."""
    from openai import AsyncOpenAI

    context = await load_report_context(db, user.id)
    history = _history_for_llm(session.messages)

    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout_seconds,
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": context.as_prompt_block()},
        *history,
        {"role": "user", "content": question},
    ]
    stream = await client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        max_tokens=settings.llm_max_tokens,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


async def stream_answer(
    db: AsyncSession, user: User, session: ChatSession, question: str
) -> AsyncIterator[str]:
    """Единая точка стриминга: LLM при наличии ключа, иначе оффлайн-советник.

    Ошибки провайдера не роняют запрос: логируем и переключаемся на оффлайн-ответ.
    """
    if settings.llm_enabled and settings.llm_api_key:
        try:
            async for delta in _stream_llm(db, user, session, question):
                yield delta
            return
        except Exception as exc:
            logger.warning("ai.llm_failed", error=str(exc))

    context = await load_report_context(db, user.id)
    all_exercises = await exercises_service.list_exercises(db)
    growth_keys = {str(m["key"]) for m in context.growth if m.get("key")}
    picked = exercises_service.recommend_by_targets(all_exercises, growth_keys, limit=4)
    answer = build_offline_answer(question, context, picked)

    for token in _tokenize(answer):
        yield token


def _tokenize(text: str, size: int = 24) -> list[str]:
    """Дробит текст на небольшие куски для эффекта печати."""
    words = text.split(" ")
    chunks: list[str] = []
    current = ""
    for word in words:
        current = f"{current} {word}".strip()
        if len(current) >= size:
            chunks.append(current + " ")
            current = ""
    if current:
        chunks.append(current)
    return chunks or [text]


async def ensure_session(db: AsyncSession, user: User, title: str | None = None) -> ChatSession:
    """Возвращает активную сессию чата или создаёт новую."""
    session = (
        await db.execute(
            select(ChatSession)
            .where(ChatSession.user_id == user.id, ChatSession.is_archived.is_(False))
            .order_by(ChatSession.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if session is not None:
        return session

    context = await load_report_context(db, user.id)
    session = ChatSession(
        user_id=user.id,
        title=title or "Консультация",
        report_id=context.report_id,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def append_message(
    db: AsyncSession,
    session: ChatSession,
    role: str,
    content: str,
    meta: dict | None = None,
) -> ChatMessage:
    """Сохраняет сообщение в сессии."""
    message = ChatMessage(session_id=session.id, role=role, content=content, meta=meta or {})
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message


async def generate_plan(db: AsyncSession, user: User, horizon_days: int = 30) -> LooksmaxPlan:
    """Создаёт looksmax-план на горизонт дней по последнему отчёту."""
    from datetime import UTC, datetime

    context = await load_report_context(db, user.id)
    all_exercises = await exercises_service.list_exercises(db)
    growth_keys = {str(m["key"]) for m in context.growth if m.get("key")}
    picked = exercises_service.recommend_by_targets(all_exercises, growth_keys, limit=6)

    title = f"План на {horizon_days} дней"
    body = build_offline_answer("Составь план на 30 дней", context, picked)
    if horizon_days != 30:
        body = f"Горизонт: {horizon_days} дней.\n\n{body}"

    plan = LooksmaxPlan(
        user_id=user.id,
        report_id=context.report_id,
        title=title,
        horizon_days=horizon_days,
        content_md=body,
        generated_at=datetime.now(UTC),
        model="offline" if not settings.llm_enabled else settings.llm_model,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan


def quick_prompts() -> list[dict[str, str]]:
    """Быстрые промпты для интерфейса чата."""
    return [dict(item) for item in QUICK_PROMPTS]


def json_event(event: str, **payload: object) -> str:
    """Формирует SSE-строку события."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
