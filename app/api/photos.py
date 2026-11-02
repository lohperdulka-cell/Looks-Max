"""Роутер фото: загрузка, landmarks, предпросмотр оценки, видимость, удаление.

Тяжёлые CV/ML-операции (декодирование, детекция, выравнивание, скоринг) уходят
в threadpool через `run_in_threadpool`, чтобы не блокировать event loop.
"""

from __future__ import annotations

from typing import Annotated

import numpy as np
from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from app.core.deps import CurrentUser, DbSession
from app.core.limiter import limiter
from app.core.storage import delete_media, resolve_media_path, save_user_image
from app.logging_config import get_logger
from app.ml.constants import DEMO_DISCLAIMER, DISCLAIMER
from app.ml.landmarks import FaceLandmarks, LandmarkError
from app.ml.metrics import compute_raw_metrics
from app.ml.preprocessing import decode_image, preprocess
from app.ml.scoring import score_metrics, strengths_and_growth
from app.models.photo import Photo
from app.models.user import User
from app.schemas.photos import (
    LandmarksPayload,
    MetricPreview,
    PhotoDetail,
    PhotoListOut,
    PhotoOut,
    PhotoVisibilityUpdate,
    PreviewOut,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/photos", tags=["photos"])


def _landmarks_to_dict(lm: FaceLandmarks) -> dict:
    """Сериализует landmarks в JSON-совместимый словарь."""
    return {
        "points": [[float(x), float(y)] for x, y in lm.points],
        "provider": lm.provider,
        "bbox": list(lm.bbox) if lm.bbox else None,
        "gender": lm.gender,
        "age": lm.age,
        "image_size": list(lm.image_size) if lm.image_size else None,
    }


def _landmarks_from_dict(data: dict) -> FaceLandmarks:
    """Восстанавливает FaceLandmarks из сохранённого словаря."""
    points = np.asarray(data["points"], dtype=np.float64)
    bbox = tuple(data["bbox"]) if data.get("bbox") else None
    size = data.get("image_size")
    image_size = (int(size[0]), int(size[1])) if size else None
    return FaceLandmarks(
        points=points,
        provider=data.get("provider", "manual"),
        gender=data.get("gender"),
        age=data.get("age"),
        bbox=bbox,
        image_size=image_size,
    )


def _metric_preview(metric) -> MetricPreview:
    return MetricPreview(
        key=metric.key,
        label=metric.label,
        category=metric.category,
        raw_value=metric.raw_value,
        reference_value=metric.reference_value,
        deviation=metric.deviation,
        score=round(metric.score, 2),
        weight=metric.weight,
        unit=metric.unit,
        source=metric.source,
        notes=metric.notes,
    )


async def _touch_user_progress(db: DbSession, user: User) -> None:
    """Отмечает активность пользователя и выдаёт достижения за загрузку фото."""
    from app.services import achievements as achievements_service
    from app.services import profile as profile_service

    await profile_service.touch_activity(db, user)
    stats = await profile_service.collect_stats(db, user)
    await achievements_service.award_achievements(
        db, user, profile_service.stats_to_achievement_stats(stats)
    )


async def _get_owned_photo(db: DbSession, photo_id: int, user: User) -> Photo:
    """Возвращает фото, если оно принадлежит пользователю (иначе 404 — не раскрываем наличие)."""
    photo = await db.get(Photo, photo_id)
    if photo is None or photo.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Фото не найдено")
    return photo


async def _load_metric_weights(db: DbSession) -> tuple[dict[str, float], dict[str, bool]]:
    """Глобальные переопределения весов/включённости метрик из админки."""
    from app.models.content import MetricWeight

    rows = (await db.execute(select(MetricWeight))).scalars().all()
    weights = {row.key: float(row.weight) for row in rows}
    enabled = {row.key: bool(row.is_enabled) for row in rows}
    return weights, enabled


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PhotoDetail)
async def upload_photo(
    user: CurrentUser,
    db: DbSession,
    file: Annotated[UploadFile, File(description="JPEG/PNG/WebP с лицом")],
) -> PhotoDetail:
    """Загружает фото, извлекает landmarks и сохраняет метаданные.

    Конвейер: magic-bytes → декодирование → landmarks → выравнивание по глазам →
    кроп → оценка качества. Результат доступен в предпросмотре и в отчёте.
    """
    stored = await save_user_image(file, user.id)

    try:
        raw = stored.absolute_path.read_bytes()
        image = await run_in_threadpool(decode_image, raw)
        result = await run_in_threadpool(preprocess, image)
    except LandmarkError as exc:
        delete_media(stored.relative_path)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Лицо не найдено на фото. Нужен анфас с ровным светом.",
        ) from exc
    except Exception as exc:
        delete_media(stored.relative_path)
        logger.error("photos.preprocess_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Не удалось обработать изображение",
        ) from exc

    lm = result.landmarks
    photo = Photo(
        user_id=user.id,
        storage_path=stored.relative_path,
        original_filename=file.filename,
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
        width=stored.width,
        height=stored.height,
        landmarks=_landmarks_to_dict(lm),
        landmarks_provider=lm.provider,
        is_aligned=result.is_aligned,
        quality_score=result.quality_score,
    )
    db.add(photo)
    await db.commit()
    await db.refresh(photo)
    await _touch_user_progress(db, user)

    logger.info(
        "photos.uploaded",
        photo_id=photo.id,
        user_id=user.id,
        provider=lm.provider,
        quality=round(result.quality_score, 2),
    )

    return PhotoDetail(
        **PhotoOut.model_validate(photo).model_dump(),
        landmarks=photo.landmarks,
        warnings=result.warnings,
        image_url=f"/api/photos/{photo.id}/image",
        is_demo=lm.provider == "demo",
    )


@router.get("", response_model=PhotoListOut)
async def list_photos(
    user: CurrentUser,
    db: DbSession,
    limit: int = 24,
    offset: int = 0,
) -> PhotoListOut:
    """Список фото пользователя (без скрытых)."""
    limit = max(1, min(limit, 100))
    base = select(Photo).where(Photo.user_id == user.id, Photo.is_hidden.is_(False))
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (await db.execute(base.order_by(Photo.created_at.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return PhotoListOut(items=[PhotoOut.model_validate(p) for p in rows], total=int(total))


@router.get("/{photo_id}", response_model=PhotoDetail)
async def get_photo(photo_id: int, user: CurrentUser, db: DbSession) -> PhotoDetail:
    """Детали фото с landmarks."""
    photo = await _get_owned_photo(db, photo_id, user)
    return PhotoDetail(
        **PhotoOut.model_validate(photo).model_dump(),
        landmarks=photo.landmarks,
        warnings=[],
        image_url=f"/api/photos/{photo.id}/image",
        is_demo=photo.landmarks_provider == "demo",
    )


@router.get("/{photo_id}/image")
async def get_photo_image(photo_id: int, user: CurrentUser, db: DbSession) -> FileResponse:
    """Отдаёт файл изображения владельцу (защита от IDOR)."""
    photo = await _get_owned_photo(db, photo_id, user)
    path = resolve_media_path(photo.storage_path)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Файл недоступен")
    return FileResponse(
        path,
        media_type=photo.content_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.patch("/{photo_id}", response_model=PhotoOut)
async def update_photo_visibility(
    photo_id: int, payload: PhotoVisibilityUpdate, user: CurrentUser, db: DbSession
) -> PhotoOut:
    """Меняет видимость фото: скрытие и opt-in публикацию в батлах."""
    photo = await _get_owned_photo(db, photo_id, user)
    if payload.is_hidden is not None:
        photo.is_hidden = payload.is_hidden
    if payload.is_public_in_battles is not None:
        photo.is_public_in_battles = payload.is_public_in_battles
    await db.commit()
    await db.refresh(photo)
    return PhotoOut.model_validate(photo)


@router.delete("/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_photo(photo_id: int, user: CurrentUser, db: DbSession) -> None:
    """Удаляет фото и файл с диска."""
    photo = await _get_owned_photo(db, photo_id, user)
    delete_media(photo.storage_path)
    await db.delete(photo)
    await db.commit()
    logger.info("photos.deleted", photo_id=photo_id, user_id=user.id)


@router.put("/{photo_id}/landmarks", response_model=PhotoDetail)
async def update_landmarks(
    photo_id: int, payload: LandmarksPayload, user: CurrentUser, db: DbSession
) -> PhotoDetail:
    """Сохраняет вручную поправленные точки (drag&drop на canvas)."""
    photo = await _get_owned_photo(db, photo_id, user)
    points = np.asarray(payload.points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 2:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный формат точек")
    if np.any((points < -0.5) | (points > 1.5)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Точки вне диапазона")

    previous = photo.landmarks or {}
    photo.landmarks = {
        "points": points.tolist(),
        "provider": "manual",
        "bbox": payload.bbox or previous.get("bbox"),
        "gender": previous.get("gender"),
        "age": previous.get("age"),
    }
    photo.landmarks_provider = "manual"
    await db.commit()
    await db.refresh(photo)
    return PhotoDetail(
        **PhotoOut.model_validate(photo).model_dump(),
        landmarks=photo.landmarks,
        warnings=[],
        image_url=f"/api/photos/{photo.id}/image",
        is_demo=photo.landmarks_provider == "demo",
    )


@router.post("/{photo_id}/preview", response_model=PreviewOut)
@limiter.limit("30/minute")
async def preview_score(
    photo_id: int,
    user: CurrentUser,
    db: DbSession,
    request: Request,
) -> PreviewOut:
    """Считает оценку по текущим landmarks без сохранения отчёта.

    Используется для мгновенного предпросмотра на canvas: пользователь двигает
    точки — метрики пересчитываются «на лету».
    """
    photo = await _get_owned_photo(db, photo_id, user)
    if not photo.landmarks:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="У фото нет landmarks")

    lm = _landmarks_from_dict(photo.landmarks)
    gender = photo.landmarks.get("gender") or user.gender
    demo = photo.landmarks_provider == "demo"
    weights, enabled = await _load_metric_weights(db)

    def _compute() -> PreviewOut:
        raw = compute_raw_metrics(lm, gender=gender)
        result = score_metrics(
            raw, gender=gender, weight_overrides=weights or None, enabled=enabled or None
        )
        strengths, growth = strengths_and_growth(result)
        return PreviewOut(
            overall=round(result.overall, 2),
            harmony=round(result.harmony, 2),
            categories={k: round(v, 2) for k, v in result.categories.items()},
            metrics=[_metric_preview(m) for m in result.metrics],
            strengths=[_metric_preview(m) for m in strengths],
            growth=[_metric_preview(m) for m in growth],
            missing=result.missing,
            disclaimer=DEMO_DISCLAIMER if demo else DISCLAIMER,
            is_demo=demo,
        )

    return await run_in_threadpool(_compute)
