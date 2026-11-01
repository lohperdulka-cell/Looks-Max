"""Сервис отчётов: расчёт, сохранение, выборка, публичные ссылки.

Здесь сводится вместе всё: landmarks фото → сырые метрики → баллы → категории →
рекомендации → запись в БД. Скоринг детерминирован, поэтому отчёт всегда можно
пересчитать из тех же landmarks.
"""

from __future__ import annotations

import secrets
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ml.constants import (
    CATEGORY_LABELS,
    CATEGORY_WEIGHTS,
    DEMO_DISCLAIMER,
    DISCLAIMER,
    METRIC_BY_KEY,
    METRIC_FORMULAS,
    MODEL_VERSION,
    VARIABLE_LABELS,
    MetricCategory,
)
from app.ml.landmarks import FaceLandmarks
from app.ml.metrics import compute_raw_metrics
from app.ml.recommendations import build_recommendations
from app.ml.scoring import ScoreResult, category_label, score_metrics
from app.models.content import MetricWeight
from app.models.photo import Photo
from app.models.report import MetricValue, Report
from app.models.user import User
from app.schemas.reports import (
    CategoryContributionOut,
    CategoryOut,
    ComputationOut,
    MetricOut,
    RecommendationOut,
    ReportOut,
    ReportSummary,
    VariableOut,
)

MAX_RECOMMENDATIONS = 6
TOP_N = 4

Overrides = tuple[dict[str, float], dict[str, float], dict[str, bool]]


async def load_metric_overrides(db: AsyncSession) -> Overrides:
    """Читает переопределения эталонов/весов/включённости метрик из админки."""
    rows = (await db.execute(select(MetricWeight))).scalars().all()
    references: dict[str, float] = {}
    weights: dict[str, float] = {}
    enabled: dict[str, bool] = {}
    for row in rows:
        if row.reference_value is not None:
            references[row.key] = float(row.reference_value)
        weights[row.key] = float(row.weight)
        enabled[row.key] = bool(row.is_enabled)
    return references, weights, enabled


def landmarks_from_photo(photo: Photo) -> FaceLandmarks:
    """Восстанавливает FaceLandmarks из JSON, сохранённого в БД."""
    if not photo.landmarks or not photo.landmarks.get("points"):
        raise ValueError("У фото нет landmarks")
    points = np.asarray(photo.landmarks["points"], dtype=np.float64)
    bbox = tuple(photo.landmarks["bbox"]) if photo.landmarks.get("bbox") else None
    return FaceLandmarks(
        points=points,
        provider=photo.landmarks.get("provider", "manual"),
        gender=photo.landmarks.get("gender"),
        age=photo.landmarks.get("age"),
        bbox=bbox,
    )


def compute_report(
    photo: Photo, user: User | None, overrides: Overrides | None = None
) -> ScoreResult:
    """Считает ScoreResult по landmarks фото с учётом переопределений из админки."""
    lm = landmarks_from_photo(photo)
    gender = lm.gender or (user.gender if user else None)
    references, weights, enabled = overrides or ({}, {}, {})
    raw = compute_raw_metrics(lm, gender=gender)
    return score_metrics(
        raw,
        gender=gender,
        overrides=references,
        weight_overrides=weights,
        enabled=enabled,
    )


def category_breakdown(result: ScoreResult) -> list[dict]:
    """Агрегаты по категориям с числом метрик, отсортированные по весу."""
    counts: dict[str, int] = {}
    for metric in result.metrics:
        counts[metric.category] = counts.get(metric.category, 0) + 1

    breakdown: list[dict] = []
    for key, score in result.categories.items():
        try:
            category = MetricCategory(key)
            weight = CATEGORY_WEIGHTS.get(category, 0.0)
            label = CATEGORY_LABELS.get(category, key)
        except ValueError:
            weight = 0.0
            label = key
        breakdown.append(
            {
                "key": key,
                "label": label,
                "score": round(float(score), 2),
                "weight": weight,
                "metrics_count": counts.get(key, 0),
            }
        )
    breakdown.sort(key=lambda item: item["weight"], reverse=True)
    return breakdown


async def create_report(
    db: AsyncSession, photo: Photo, user: User, overrides: Overrides | None = None
) -> Report:
    """Считает и сохраняет отчёт вместе со значениями метрик."""
    result = compute_report(photo, user, overrides)
    recommendations = build_recommendations(result, limit=MAX_RECOMMENDATIONS)
    breakdown = category_breakdown(result)

    report = Report(
        user_id=user.id,
        photo_id=photo.id,
        overall_score=result.overall,
        harmony_score=result.harmony,
        categories={item["key"]: item["score"] for item in breakdown},
        summary={
            "missing": result.missing,
            "categories_detail": breakdown,
            "is_demo": photo.landmarks_provider == "demo",
            # Промежуточные величины и вклад категорий: отчёт можно проверить
            # вручную, не пересчитывая геометрию заново.
            "variables": {
                key: float(value)
                for key, value in result.auxiliary.items()
                if key in VARIABLE_LABELS
            },
            "category_contributions": result.category_contributions,
        },
        recommendations={
            "items": [
                {
                    "key": rec.key,
                    "label": rec.label,
                    "category": rec.category,
                    "priority": rec.priority,
                    "severity": rec.severity,
                    "direction": rec.direction,
                    "text": rec.text,
                    "actions": rec.actions,
                    "source": rec.source,
                }
                for rec in recommendations
            ]
        },
        model_version=result.model_version or MODEL_VERSION,
    )
    db.add(report)
    await db.flush()

    for metric in result.metrics:
        db.add(
            MetricValue(
                report_id=report.id,
                key=metric.key,
                label=metric.label,
                category=metric.category,
                raw_value=metric.raw_value,
                reference_value=metric.reference_value,
                deviation=metric.deviation,
                score=metric.score,
                weight=metric.weight,
                extra={"unit": metric.unit, "source": metric.source, "notes": metric.notes},
            )
        )

    await db.commit()
    # Перечитываем с метриками: после commit ленивая загрузка в async недоступна.
    return await get_owned_report(db, report.id, user.id)  # type: ignore[return-value]


async def list_reports(
    db: AsyncSession, user_id: int, limit: int = 20, offset: int = 0
) -> tuple[list[Report], int]:
    """История отчётов пользователя (новые сверху)."""
    limit = max(1, min(limit, 100))
    base = select(Report).where(Report.user_id == user_id)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (
            await db.execute(
                base.order_by(Report.created_at.desc())
                .limit(limit)
                .offset(offset)
                .options(selectinload(Report.metrics))
            )
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


async def get_owned_report(db: AsyncSession, report_id: int, user_id: int) -> Report | None:
    """Отчёт пользователя по id с метриками (None, если чужой или отсутствует).

    Метрики подгружаются сразу: в async-сессии ленивая загрузка недопустима.
    """
    report = (
        await db.execute(
            select(Report)
            .where(Report.id == report_id, Report.user_id == user_id)
            .options(selectinload(Report.metrics), selectinload(Report.photo))
        )
    ).scalar_one_or_none()
    return report


async def get_public_report(db: AsyncSession, token: str) -> Report | None:
    """Публичный отчёт по анонимному токену."""
    if not token or len(token) > 64:
        return None
    report = (
        await db.execute(
            select(Report)
            .where(Report.public_token == token, Report.is_public.is_(True))
            .options(selectinload(Report.metrics), selectinload(Report.photo))
        )
    ).scalar_one_or_none()
    return report


def serialize_metrics(report: Report) -> list[MetricOut]:
    """Метрики отчёта в схему API (по возрастанию балла).

    Допуск и формула берутся из спецификаций, а не из БД: они неизменны для
    версии модели и нужны только для объяснения расчёта в интерфейсе.
    """
    out: list[MetricOut] = []
    for metric in report.metrics:
        extra = metric.extra or {}
        spec = METRIC_BY_KEY.get(metric.key)
        tolerance = spec.tolerance if spec else 0.0
        deviation = metric.deviation
        z_score = abs(deviation) / tolerance if deviation is not None and tolerance > 0 else 0.0
        out.append(
            MetricOut(
                key=metric.key,
                label=metric.label,
                category=metric.category,
                category_label=category_label(metric.category),
                raw_value=metric.raw_value,
                reference_value=metric.reference_value,
                deviation=deviation,
                score=round(metric.score, 2),
                weight=metric.weight,
                unit=str(extra.get("unit", "")),
                source=str(extra.get("source", "")),
                notes=str(extra.get("notes", "")),
                tolerance=tolerance,
                z_score=round(z_score, 3),
                formula=METRIC_FORMULAS.get(metric.key, ""),
            )
        )
    out.sort(key=lambda m: m.score)
    return out


def split_strengths_growth(metrics: list[MetricOut]) -> tuple[list[MetricOut], list[MetricOut]]:
    """Делит метрики на сильные стороны и зоны роста (без композита harmony)."""
    scored = [m for m in metrics if m.key != "harmony"]
    ordered = sorted(scored, key=lambda m: m.score, reverse=True)
    strengths = ordered[:TOP_N]
    growth = list(reversed(ordered[-TOP_N:])) if len(ordered) > TOP_N else list(reversed(ordered))
    return strengths, growth


def _categories_detail(report: Report) -> list[CategoryOut]:
    summary = report.summary or {}
    return [CategoryOut(**item) for item in summary.get("categories_detail", [])]


def _recommendations(report: Report) -> list[RecommendationOut]:
    return [RecommendationOut(**item) for item in (report.recommendations or {}).get("items", [])]


def is_demo_report(report: Report) -> bool:
    """Отчёт построен на синтетических точках (детектор лица не установлен)."""
    return bool((report.summary or {}).get("is_demo"))


def _report_landmarks(report: Report) -> dict[str, Any] | None:
    """Точки лица отчёта: нужны для разметки метрик поверх фото."""
    photo = getattr(report, "photo", None)
    if photo is None or not photo.landmarks or not photo.landmarks.get("points"):
        return None
    return photo.landmarks


def _computation(report: Report, metrics: list[MetricOut]) -> ComputationOut:
    """Собирает блок прозрачности: переменные, вклад категорий, формула итога.

    Переменные и вклады лежат в summary (записываются при создании отчёта).
    Для старых отчётов их там нет — тогда блок остаётся пустым, а метрики
    всё равно показывают допуск и формулу.
    """
    summary = report.summary or {}
    raw_variables = summary.get("variables") or {}
    variables = [
        VariableOut(key=key, label=VARIABLE_LABELS.get(key, key), value=float(value))
        for key, value in sorted(raw_variables.items())
    ]

    contributions_raw = summary.get("category_contributions") or {}
    contributions: list[CategoryContributionOut] = []
    for item in _categories_detail(report):
        contribution = float(contributions_raw.get(item.key, 0.0))
        contributions.append(
            CategoryContributionOut(
                key=item.key,
                label=item.label,
                score=item.score,
                weight=item.weight,
                contribution=round(contribution, 3),
                # Доля категории в сумме вкладов: показывает, что сильнее влияет.
                share=round(contribution / sum(contributions_raw.values()), 4)
                if contributions_raw and sum(contributions_raw.values()) > 0
                else 0.0,
            )
        )

    numerator = round(sum(float(v) for v in contributions_raw.values()), 3)
    denominator = round(
        sum(item.weight for item in _categories_detail(report) if item.weight > 0), 3
    )
    return ComputationOut(
        variables=variables,
        category_contributions=contributions,
        overall_formula="Σ(score_category × weight_category) / Σ(weight_category)",
        overall_numerator=numerator,
        overall_denominator=denominator,
    )


def serialize_report(report: Report) -> ReportOut:
    """Полный отчёт в схему API."""
    metrics = serialize_metrics(report)
    strengths, growth = split_strengths_growth(metrics)
    demo = is_demo_report(report)
    return ReportOut(
        **ReportSummary.model_validate(report).model_dump(),
        categories_detail=_categories_detail(report),
        metrics=metrics,
        recommendations=_recommendations(report),
        strengths=strengths,
        growth=growth,
        disclaimer=DEMO_DISCLAIMER if demo else DISCLAIMER,
        image_url=f"/api/photos/{report.photo_id}/image",
        is_demo=demo,
        computation=_computation(report, metrics),
        landmarks=_report_landmarks(report),
    )


def ensure_public_token(report: Report) -> str:
    """Гарантирует наличие публичного токена у отчёта."""
    if not report.public_token:
        report.public_token = secrets.token_urlsafe(16)
    return report.public_token
