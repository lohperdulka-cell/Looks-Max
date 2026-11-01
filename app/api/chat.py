"""Роутер ИИ-чата: SSE-стриминг ответа, история, looksmax-планы."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.deps import CurrentUser, DbSession
from app.core.limiter import limiter
from app.models.chat import ChatSession, LooksmaxPlan
from app.schemas.chat import (
    ChatMetaOut,
    ChatRequest,
    ChatSessionOut,
    PlanListOut,
    PlanOut,
    PlanRequest,
    QuickPromptOut,
)
from app.services import ai as ai_service

router = APIRouter(prefix="/api/chat", tags=["chat"])


async def _owned_session(db: DbSession, session_id: int, user_id: int) -> ChatSession:
    """Сессия чата с проверкой владельца и подгруженной историей."""
    session = (
        await db.execute(
            select(ChatSession)
            .options(selectinload(ChatSession.messages))
            .where(ChatSession.id == session_id)
        )
    ).scalar_one_or_none()
    if session is None or session.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Диалог не найден")
    return session


@router.get("/meta", response_model=ChatMetaOut)
async def meta(user: CurrentUser) -> ChatMetaOut:
    """Доступность LLM и список быстрых промптов."""
    return ChatMetaOut(
        llm_enabled=bool(settings.llm_enabled and settings.llm_api_key),
        quick_prompts=[QuickPromptOut(**item) for item in ai_service.quick_prompts()],
    )


@router.get("/session", response_model=ChatSessionOut)
async def current_session(user: CurrentUser, db: DbSession) -> ChatSessionOut:
    """Активная сессия чата с историей (создаётся при необходимости)."""
    session = await ai_service.ensure_session(db, user)
    session = await _owned_session(db, session.id, user.id)
    return ChatSessionOut(
        id=session.id,
        title=session.title,
        report_id=session.report_id,
        created_at=session.created_at,
        messages=[
            {"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at}
            for m in session.messages
            if m.role in {"user", "assistant"}
        ],
    )


@router.post("/stream")
@limiter.limit("20/minute")
async def stream(
    request: Request, payload: ChatRequest, user: CurrentUser, db: DbSession
) -> StreamingResponse:
    """Стримит ответ ассистента как Server-Sent Events.

    Формат событий:
      event: meta   — {session_id}
      event: delta  — {text}
      event: done   — {message_id, offline}
      event: error  — {detail}
    """
    if payload.session_id is not None:
        session = await _owned_session(db, payload.session_id, user.id)
    else:
        session = await ai_service.ensure_session(db, user)

    await ai_service.append_message(db, session, "user", payload.message)

    async def event_stream():
        collected: list[str] = []
        yield ai_service.json_event("meta", session_id=session.id)
        try:
            async for delta in ai_service.stream_answer(db, user, session, payload.message):
                collected.append(delta)
                yield ai_service.json_event("delta", text=delta)
        except Exception as exc:
            yield ai_service.json_event("error", detail=str(exc))

        answer = "".join(collected).strip()
        if answer:
            message = await ai_service.append_message(db, session, "assistant", answer)
            message_id = message.id
        else:
            message_id = None
        yield ai_service.json_event(
            "done",
            message_id=message_id,
            offline=not (settings.llm_enabled and settings.llm_api_key),
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(payload: PlanRequest, user: CurrentUser, db: DbSession) -> PlanOut:
    """Генерирует и сохраняет looksmax-план по последнему отчёту."""
    plan = await ai_service.generate_plan(db, user, horizon_days=payload.horizon_days)
    return PlanOut.model_validate(plan)


@router.get("/plans", response_model=PlanListOut)
async def list_plans(user: CurrentUser, db: DbSession) -> PlanListOut:
    """История looksmax-планов пользователя."""
    plans = (
        (
            await db.execute(
                select(LooksmaxPlan)
                .where(LooksmaxPlan.user_id == user.id)
                .order_by(LooksmaxPlan.generated_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return PlanListOut(items=[PlanOut.model_validate(p) for p in plans], total=len(plans))
