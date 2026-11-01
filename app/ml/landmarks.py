"""Детекция лица и извлечение landmark-точек.

Основной провайдер — InsightFace (buffalo_l): даёт 106 точек (2d106det) плюс
пол/возраст через genderage. Fallback — MediaPipe FaceMesh (468 точек).

Почему InsightFace основной:
  * landmarks 106 точек точнее на контурах челюсти/глаз, что критично для
    gonial angle и canthal tilt;
  * встроенный genderage даёт независимую оценку пола/возраста для метрики
    диморфизма (не только геометрия);
  * одна модель = одна зависимость (onnxruntime), предсказуемое качество.

Почему MediaPipe как fallback:
  * не требует загрузки весов из сети, легче по весу, работает на CPU;
  * 468 точек дают плотный контур, но хуже калиброван по антропометрии.

Модели грузятся лениво и кэшируются в памяти процесса (lazy-load).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

# Индексы ключевых точек InsightFace 2d106det (1-based в оригинале, здесь 0-based).
# Источник соответствия: insightface/python-package/insightface/app/common.py + docs.
IF_LEFT_EYE = (35, 33)  # внутренний, наружный угол левого глаза (со стороны изображения)
IF_RIGHT_EYE = (89, 87)
IF_LEFT_BROW = (43, 46)
IF_RIGHT_BROW = (97, 100)
IF_NOSE_TIP = 86
IF_NOSE_LEFT = 74
IF_NOSE_RIGHT = 98
IF_MOUTH_LEFT = 60
IF_MOUTH_RIGHT = 105  # правый угол рта
IF_UPPER_LIP = 62
IF_LOWER_LIP = 66
IF_CHIN = 16
IF_JAW_LEFT = 1
IF_JAW_RIGHT = 15
IF_FOREHEAD_TOP = 27  # трихион (приближение)

_landmark_lock = threading.Lock()
_insightface_app: Any | None = None
_mediapipe_mesh: Any | None = None
_insightface_failed = False
_mediapipe_failed = False


@dataclass(slots=True)
class FaceLandmarks:
    """Результат извлечения landmarks."""

    points: np.ndarray  # shape (N, 2), нормализованные координаты 0..1
    provider: str
    gender: str | None = None  # male | female
    age: float | None = None
    bbox: tuple[float, float, float, float] | None = None  # x1,y1,x2,y2 (норм.)
    extra: dict[str, Any] = field(default_factory=dict)


class LandmarkError(RuntimeError):
    """Не удалось извлечь landmarks (лицо не найдено или провайдеры недоступны)."""


def _load_insightface() -> Any | None:
    """Ленивая загрузка InsightFace FaceAnalysis с кэшем в памяти."""
    global _insightface_app, _insightface_failed
    if _insightface_app is not None or _insightface_failed:
        return _insightface_app
    with _landmark_lock:
        if _insightface_app is not None or _insightface_failed:
            return _insightface_app
        try:
            from insightface.app import FaceAnalysis

            settings.ml_model_dir.mkdir(parents=True, exist_ok=True)
            app = FaceAnalysis(
                name="buffalo_l",
                root=str(settings.ml_model_dir),
                providers=["CPUExecutionProvider"],
            )
            app.prepare(ctx_id=-1, det_size=(640, 640))
            _insightface_app = app
            logger.info("ml.insightface.loaded", root=str(settings.ml_model_dir))
        except Exception as exc:
            _insightface_failed = True
            logger.warning("ml.insightface.unavailable", error=str(exc))
        return _insightface_app


def _load_mediapipe() -> Any | None:
    """Ленивая загрузка MediaPipe FaceMesh."""
    global _mediapipe_mesh, _mediapipe_failed
    if _mediapipe_mesh is not None or _mediapipe_failed:
        return _mediapipe_mesh
    with _landmark_lock:
        if _mediapipe_mesh is not None or _mediapipe_failed:
            return _mediapipe_mesh
        try:
            import mediapipe as mp

            _mediapipe_mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=True,
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=0.5,
            )
            logger.info("ml.mediapipe.loaded")
        except Exception as exc:
            _mediapipe_failed = True
            logger.warning("ml.mediapipe.unavailable", error=str(exc))
        return _mediapipe_mesh


def _extract_with_insightface(image_bgr: np.ndarray) -> FaceLandmarks | None:
    app = _load_insightface()
    if app is None:
        return None
    faces = app.get(image_bgr)
    if not faces:
        return None
    face = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    h, w = image_bgr.shape[:2]

    pts: np.ndarray | None = None
    for attr in ("landmark_2d_106", "landmark_3d_68", "kps"):
        candidate = getattr(face, attr, None)
        if candidate is not None and len(candidate) >= 5:
            pts = np.asarray(candidate, dtype=np.float64)[:, :2]
            break
    if pts is None:
        return None

    pts[:, 0] /= w
    pts[:, 1] /= h
    x1, y1, x2, y2 = face.bbox
    gender = None
    if getattr(face, "gender", None) is not None:
        gender = "male" if int(face.gender) == 1 else "female"
    return FaceLandmarks(
        points=pts,
        provider="insightface",
        gender=gender,
        age=float(face.age) if getattr(face, "age", None) is not None else None,
        bbox=(x1 / w, y1 / h, x2 / w, y2 / h),
    )


def _extract_with_mediapipe(image_bgr: np.ndarray) -> FaceLandmarks | None:
    mesh = _load_mediapipe()
    if mesh is None:
        return None
    import cv2

    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    result = mesh.process(rgb)
    if not result.multi_face_landmarks:
        return None
    lm = result.multi_face_landmarks[0]
    pts = np.array([[p.x, p.y] for p in lm.landmark], dtype=np.float64)
    xs, ys = pts[:, 0], pts[:, 1]
    return FaceLandmarks(
        points=pts,
        provider="mediapipe",
        bbox=(
            float(np.min(xs)),
            float(np.min(ys)),
            float(np.max(xs)),
            float(np.max(ys)),
        ),
    )


def extract_landmarks(image_bgr: np.ndarray) -> FaceLandmarks:
    """Извлекает landmarks: InsightFace, при неудаче — MediaPipe.

    Бросает LandmarkError, если ни один провайдер не смог найти лицо.
    """
    if not settings.ml_enabled:
        raise LandmarkError("ML отключён в настройках (ML_ENABLED=false)")

    primary = settings.ml_provider_primary
    fallback = settings.ml_provider_fallback

    order = [primary, fallback] if primary != fallback else [primary]
    last_error: Exception | None = None

    for provider in order:
        try:
            result = (
                _extract_with_insightface(image_bgr)
                if provider == "insightface"
                else _extract_with_mediapipe(image_bgr)
            )
        except Exception as exc:
            last_error = exc
            logger.warning("ml.provider.error", provider=provider, error=str(exc))
            continue
        if result is not None and len(result.points) >= 5:
            logger.info("ml.landmarks.extracted", provider=result.provider, n=len(result.points))
            return result

    raise LandmarkError(f"Лицо не найдено (провайдеры: {', '.join(order)})") from last_error


def landmarks_to_list(lm: FaceLandmarks) -> list[list[float]]:
    """Сериализует точки для хранения в JSON (округление до 6 знаков)."""
    return [[round(float(x), 6), round(float(y), 6)] for x, y in lm.points]


def list_to_landmarks(points: list[list[float]]) -> np.ndarray:
    """Восстанавливает массив точек из JSON."""
    return np.asarray(points, dtype=np.float64)


def reset_model_cache() -> None:
    """Сбрасывает кэш моделей (для тестов и перезагрузки)."""
    global _insightface_app, _mediapipe_mesh, _insightface_failed, _mediapipe_failed
    with _landmark_lock:
        _insightface_app = None
        _mediapipe_mesh = None
        _insightface_failed = False
        _mediapipe_failed = False
