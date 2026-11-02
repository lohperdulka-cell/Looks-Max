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
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

# Семантические индексы точек конкретной схемы (InsightFace 2d106, MediaPipe
# FaceMesh) живут в app/ml/metrics.py рядом с кодом, который их использует.
# Держать здесь второй, «справочный» набор нельзя: он расходился с рабочим
# (например, углы рта 60/105 вместо 52/61) и при первом же импорте молча
# вернул бы исправленный баг обратно.

_landmark_lock = threading.Lock()
_insightface_app: Any | None = None
_mediapipe_mesh: Any | None = None
_insightface_failed = False
_mediapipe_failed = False

# Модель FaceLandmarker весит ~3.6 МБ. Файл меньше мегабайта — это обрыв
# скачивания или HTML-заглушка прокси, а не модель.
_MIN_MEDIAPIPE_MODEL_BYTES = 1_000_000

# Соответствие файлов buffalo_l задачам InsightFace. Нужно, чтобы физически
# оставить только требуемые модели: полный набор даёт пик ~920 МБ RSS.
_IF_TASK_BY_FILE = {
    "det_10g.onnx": "detection",
    "2d106det.onnx": "landmark_2d_106",
    "genderage.onnx": "genderage",
    "1k3d68.onnx": "landmark_3d_68",
    "w600k_r50.onnx": "recognition",
}


@dataclass(slots=True)
class FaceLandmarks:
    """Результат извлечения landmarks."""

    points: np.ndarray  # shape (N, 2), нормализованные координаты 0..1
    provider: str
    gender: str | None = None  # male | female
    age: float | None = None
    bbox: tuple[float, float, float, float] | None = None  # x1,y1,x2,y2 (норм.)
    # Размер изображения (w, h), к которому привязаны нормализованные координаты.
    # Нужен, чтобы вернуть точки в физическую (изотропную) систему: нормализация
    # по осям независимо сжимает x и y по-разному, и углы/пропорции получаются
    # искажёнными пропорцией кадра. None — размер неизвестен.
    image_size: tuple[int, int] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class LandmarkError(RuntimeError):
    """Не удалось извлечь landmarks (лицо не найдено или провайдеры недоступны)."""


def _pruned_buffalo_dir() -> Path:
    """Каталог buffalo_l только с нужными моделями (симлинки на оригинал).

    InsightFace при загрузке создаёт ONNX-сессию для каждого .onnx до проверки
    `allowed_modules`, поэтому лишние 300 МБ весов всё равно поднимают пик RSS.
    Оставляем только модели из `settings.ml_insightface_modules`, подсовывая
    FaceAnalysis отдельный каталог: он делает glob по нему и грузит ровно то,
    что нужно.
    """
    source = settings.ml_model_dir / "models" / "buffalo_l"
    keep = {f for f, task in _IF_TASK_BY_FILE.items() if task in settings.ml_insightface_modules}
    pruned = settings.ml_model_dir / "models" / "buffalo_l_pruned"
    pruned.mkdir(parents=True, exist_ok=True)

    for name in keep:
        src = source / name
        if not src.exists():
            continue
        dst = pruned / name
        if dst.exists():
            continue
        try:
            dst.symlink_to(src.resolve())
        except OSError:
            # Файловая система без симлинков — копируем (вес этих трёх моделей мал).
            import shutil

            shutil.copy2(src, dst)

    # Убираем ранее скопированные лишние файлы, если набор сузили.
    for existing in pruned.glob("*.onnx"):
        if existing.name not in keep:
            existing.unlink(missing_ok=True)

    return pruned


def _ensure_buffalo_files() -> None:
    """Скачивает и распаковывает buffalo_l, НЕ создавая ONNX-сессии.

    Именно из-за создания сессий полный набор моделей даёт пик ~920 МБ, поэтому
    здесь только подготовка файлов на диске (`ensure_available` = download).
    """
    from insightface.utils import ensure_available

    ensure_available("models", "buffalo_l", root=str(settings.ml_model_dir))


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
            _ensure_buffalo_files()
            model_dir = _pruned_buffalo_dir()
            app = FaceAnalysis(
                name=str(model_dir),
                providers=["CPUExecutionProvider"],
                allowed_modules=list(settings.ml_insightface_modules),
            )
            app.prepare(ctx_id=-1, det_size=(640, 640))
            _insightface_app = app
            logger.info(
                "ml.insightface.loaded",
                root=str(model_dir),
                modules=",".join(sorted(app.models)),
            )
        except Exception as exc:
            _insightface_failed = True
            logger.warning("ml.insightface.unavailable", error=str(exc))
        return _insightface_app


def _mediapipe_task_model_path() -> Path:
    """Путь к .task-модели FaceLandmarker для MediaPipe Tasks API."""
    return settings.ml_model_dir / "face_landmarker.task"


def _ensure_mediapipe_task_model() -> Path | None:
    """Гарантирует наличие .task-модели, скачивая её при необходимости.

    MediaPipe 0.10+ (в т.ч. 1.x) удалил `mp.solutions`, оставив Tasks API,
    которому нужен файл модели. Кэшируем его в ml_model_dir.
    """
    path = _mediapipe_task_model_path()
    # Порог отсекает обрезанные и пустые файлы: скачивание может прерваться,
    # а exists() для такого файла истинно, и MediaPipe упал бы при инициализации.
    if path.exists() and path.stat().st_size >= _MIN_MEDIAPIPE_MODEL_BYTES:
        return path
    if not settings.ml_mediapipe_allow_download:
        return None
    try:
        import urllib.request

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".task.tmp")
        with urllib.request.urlopen(settings.ml_mediapipe_model_url, timeout=60) as resp:
            tmp.write_bytes(resp.read())
        if tmp.stat().st_size < _MIN_MEDIAPIPE_MODEL_BYTES:
            raise RuntimeError(f"модель повреждена: {tmp.stat().st_size} байт")
        tmp.replace(path)
        logger.info("ml.mediapipe.model_downloaded", path=str(path))
        return path
    except Exception as exc:
        logger.warning("ml.mediapipe.model_download_failed", error=str(exc))
        return None


def _load_mediapipe() -> Any | None:
    """Ленивая загрузка MediaPipe FaceMesh.

    Поддерживает оба API: старый `mp.solutions.face_mesh` (<= 0.10.x) и новый
    Tasks API `mp.tasks.vision.FaceLandmarker` (0.10+/1.x), где `solutions`
    отсутствует. Возвращает объект-обёртку с единым интерфейсом `detect(rgb)`.
    """
    global _mediapipe_mesh, _mediapipe_failed
    if _mediapipe_mesh is not None or _mediapipe_failed:
        return _mediapipe_mesh
    with _landmark_lock:
        if _mediapipe_mesh is not None or _mediapipe_failed:
            return _mediapipe_mesh
        try:
            import mediapipe as mp

            solutions = getattr(mp, "solutions", None)
            if solutions is not None:
                _mediapipe_mesh = solutions.face_mesh.FaceMesh(
                    static_image_mode=True,
                    max_num_faces=1,
                    refine_landmarks=True,
                    min_detection_confidence=0.5,
                )
                logger.info("ml.mediapipe.loaded", api="solutions")
                return _mediapipe_mesh

            model_path = _ensure_mediapipe_task_model()
            if model_path is None:
                raise RuntimeError("нет .task-модели для MediaPipe Tasks API")

            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision as mp_vision

            options = mp_vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
                running_mode=mp_vision.RunningMode.IMAGE,
                num_faces=1,
                min_face_detection_confidence=0.5,
            )
            _mediapipe_mesh = _MediaPipeTaskWrapper(
                mp_vision.FaceLandmarker.create_from_options(options), mp
            )
            logger.info("ml.mediapipe.loaded", api="tasks")
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
        image_size=(w, h),
    )


class _MediaPipeTaskWrapper:
    """Единый интерфейс для `mp.tasks.vision.FaceLandmarker`.

    Приводит результат Tasks API к тому же виду, что и старый FaceMesh.process:
    объект с атрибутом `multi_face_landmarks` (список объектов с `.landmark`).
    """

    def __init__(self, landmarker: Any, mp_module: Any) -> None:
        self._landmarker = landmarker
        self._mp = mp_module

    def process(self, rgb: np.ndarray) -> Any:
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect(mp_image)

        class _Point:
            __slots__ = ("x", "y")

            def __init__(self, x: float, y: float) -> None:
                self.x = x
                self.y = y

        class _Face:
            __slots__ = ("landmark",)

            def __init__(self, points: list) -> None:
                self.landmark = points

        class _Result:
            __slots__ = ("multi_face_landmarks",)

            def __init__(self, faces: list) -> None:
                self.multi_face_landmarks = faces

        faces = [_Face([_Point(p.x, p.y) for p in face]) for face in (result.face_landmarks or [])]
        return _Result(faces)


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
    h, w = image_bgr.shape[:2]
    return FaceLandmarks(
        points=pts,
        provider="mediapipe",
        bbox=(
            float(np.min(xs)),
            float(np.min(ys)),
            float(np.max(xs)),
            float(np.max(ys)),
        ),
        image_size=(w, h),
    )


def _extract_with_demo(image_bgr: np.ndarray) -> FaceLandmarks | None:
    """Демо-провайдер: синтетическая сетка лица (см. demo_landmarks)."""
    from app.ml.demo_landmarks import synthetic_bbox, synthetic_points

    h, w = image_bgr.shape[:2]
    points = synthetic_points(image_bgr)
    return FaceLandmarks(
        points=points,
        provider="demo",
        bbox=synthetic_bbox(points),
        image_size=(w, h),
        extra={"demo": True},
    )


_EXTRACTORS: dict[str, Any] = {
    "insightface": lambda img: _extract_with_insightface(img),
    "mediapipe": lambda img: _extract_with_mediapipe(img),
    "demo": lambda img: _extract_with_demo(img),
}


def extract_landmarks(image_bgr: np.ndarray) -> FaceLandmarks:
    """Извлекает landmarks: InsightFace → MediaPipe → demo.

    Бросает LandmarkError, если ML выключен или не сработал ни один провайдер.
    Провайдер `demo` (синтетическая сетка) включается только когда в списке
    провайдеров явно задан DEMO — он не измеряет реальное лицо, а проверяет
    конвейер на деплое без тяжёлых ML-зависимостей.
    """
    if not settings.ml_enabled:
        raise LandmarkError("ML отключён в настройках (ML_ENABLED=false)")

    order = [settings.ml_provider_primary, settings.ml_provider_fallback]
    if settings.ml_provider_demo:
        order.append("demo")

    last_error: Exception | None = None
    seen: set[str] = set()
    for provider in order:
        if not provider or provider in seen or provider not in _EXTRACTORS:
            continue
        seen.add(provider)
        try:
            result = _EXTRACTORS[provider](image_bgr)
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
