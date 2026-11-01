"""Роутер отчётов: создание, история, детали, публичная ссылка, экспорт."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from starlette.concurrency import run_in_threadpool

from app.core.deps import CurrentUser, DbSession
from app.core.limiter import limiter
from app.models.photo import Photo
from app.schemas.reports import (
    PublicReportOut,
    ReportListOut,
    ReportOut,
    ReportSummary,
    ShareUpdate,
)
from app.services import achievements as achievements_service
from app.services import profile as profile_service
from app.services import reports as reports_service
from app.services.pdf import render_pdf, render_report_html

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ReportOut)
@limiter.limit("20/minute")
async def create_report(
    request: Request,
    user: CurrentUser,
    db: DbSession,
    photo_id: int,
) -> ReportOut:
    """Создаёт отчёт по фото: метрики, категории, рекомендации."""
    photo = await db.get(Photo, photo_id)
    if photo is None or photo.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Фото не найдено")
    if not photo.landmarks:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Сначала загрузите фото с landmarks"
        )

    overrides = await reports_service.load_metric_overrides(db)
    report = await reports_service.create_report(db, photo, user, overrides)

    # Активность обновляет streak; достижения выдаются по текущей статистике.
    await profile_service.touch_activity(db, user)
    stats = await profile_service.collect_stats(db, user)
    await profile_service.upsert_snapshot(db, user, stats)
    await achievements_service.award_achievements(
        db, user, profile_service.stats_to_achievement_stats(stats)
    )

    return reports_service.serialize_report(report)


@router.get("", response_model=ReportListOut)
async def list_reports(
    user: CurrentUser, db: DbSession, limit: int = 20, offset: int = 0
) -> ReportListOut:
    """История отчётов пользователя."""
    rows, total = await reports_service.list_reports(db, user.id, limit=limit, offset=offset)
    return ReportListOut(
        items=[ReportSummary.model_validate(r) for r in rows],
        total=total,
    )


@router.get("/{report_id}", response_model=ReportOut)
async def get_report(report_id: int, user: CurrentUser, db: DbSession) -> ReportOut:
    """Полный отчёт владельца."""
    report = await reports_service.get_owned_report(db, report_id, user.id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Отчёт не найден")
    return reports_service.serialize_report(report)


@router.patch("/{report_id}/share", response_model=ReportSummary)
async def toggle_share(
    report_id: int, payload: ShareUpdate, user: CurrentUser, db: DbSession
) -> ReportSummary:
    """Включает или выключает публичную анонимную ссылку на отчёт."""
    report = await reports_service.get_owned_report(db, report_id, user.id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Отчёт не найден")

    report.is_public = payload.is_public
    if payload.is_public:
        reports_service.ensure_public_token(report)
    await db.commit()
    await db.refresh(report)
    return ReportSummary.model_validate(report)


@router.get("/{report_id}/export/pdf")
async def export_pdf(report_id: int, user: CurrentUser, db: DbSession) -> Response:
    """Экспорт отчёта в PDF (WeasyPrint)."""
    report = await reports_service.get_owned_report(db, report_id, user.id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Отчёт не найден")

    payload = reports_service.serialize_report(report)
    try:
        pdf_bytes = await run_in_threadpool(render_pdf, payload, user.display_name or user.username)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    filename = f"facemetric-report-{report.id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{report_id}/export/html")
async def export_html(report_id: int, user: CurrentUser, db: DbSession) -> Response:
    """HTML-версия отчёта (для печати из браузера)."""
    report = await reports_service.get_owned_report(db, report_id, user.id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Отчёт не найден")
    payload = reports_service.serialize_report(report)
    html = render_report_html(payload, username=user.display_name or user.username)
    return Response(content=html, media_type="text/html; charset=utf-8")


# Публичный роутер: без аутентификации, только по токену.
public_router = APIRouter(prefix="/api/public", tags=["public"])


@public_router.get("/reports/{token}", response_model=PublicReportOut)
async def get_public_report(token: str, db: DbSession) -> PublicReportOut:
    """Публичный отчёт по анонимной ссылке. Персональные данные не раскрываются."""
    report = await reports_service.get_public_report(db, token)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Отчёт не найден")

    payload = reports_service.serialize_report(report)
    return PublicReportOut(
        overall_score=payload.overall_score,
        harmony_score=payload.harmony_score,
        categories_detail=payload.categories_detail,
        metrics=payload.metrics,
        recommendations=payload.recommendations,
        strengths=payload.strengths,
        growth=payload.growth,
        disclaimer=payload.disclaimer,
        model_version=payload.model_version,
        created_at=payload.created_at,
        is_demo=payload.is_demo,
    )
