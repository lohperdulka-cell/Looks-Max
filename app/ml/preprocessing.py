"""Предобработка изображения: декодирование, выравнивание по глазам, кроп, оценка качества.

Выравнивание по линии глаз (eye-line) убирает наклон головы — иначе canthal tilt
и симметрия считаются с систематической ошибкой. Кроп оставляет поля вокруг лица,
чтобы контуры челюсти не обрезались.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from app.ml.constants import MAX_HEAD_TILT_DEG, MIN_FACE_SIZE_PX, MIN_SHARPNESS
from app.ml.landmarks import FaceLandmarks, LandmarkError, extract_landmarks


@dataclass(slots=True)
class PreprocessResult:
    """Результат предобработки: выровненное изображение + метрики качества."""

    image_bgr: np.ndarray
    landmarks: FaceLandmarks
    quality_score: float
    is_aligned: bool
    warnings: list[str]
    eye_angle_deg: float


def decode_image(data: bytes) -> np.ndarray:
    """Декодирует байты в BGR-изображение (OpenCV)."""
    arr = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise LandmarkError("Не удалось декодировать изображение")
    return image


def _eye_centers(lm: FaceLandmarks) -> tuple[np.ndarray, np.ndarray]:
    """Возвращает центры левого и правого глаза (в пикселях нормированной системы).

    Схема определяется числом точек, а не провайдером: демо-провайдер отдаёт те
    же 106 точек, что и InsightFace, а отредактированные вручную точки сохраняют
    исходную плотность. Выбор по имени провайдера отправлял демо-точки в
    fallback `pts[0] / pts[-1]` и поворачивал лицо на ~113°.

    Порядок всегда «слева направо по изображению»: при поднятой голове линия
    глаз почти горизонтальна, поэтому угол наклона обязан быть малым. Схемы
    нумеруют левый и правый глаз по-разному, и без нормализации порядка
    получался разворот на ~180°.
    """
    pts = lm.points
    n = len(pts)
    if 106 <= n < 468:
        pair = ((pts[35] + pts[33]) / 2.0, (pts[89] + pts[87]) / 2.0)
    elif n >= 474:
        pair = (pts[468], pts[473])
    elif n >= 168:
        # FaceMesh без зрачков: усредняем характерные точки глаз.
        pair = (
            pts[[33, 133, 159, 145]].mean(axis=0),
            pts[[362, 263, 386, 374]].mean(axis=0),
        )
    elif n >= 48:
        # dlib 68: 36..41 — правый глаз, 42..47 — левый (в терминах субъекта).
        pair = (pts[36:42].mean(axis=0), pts[42:48].mean(axis=0))
    else:
        pair = (pts[0], pts[-1])

    left, right = pair
    if left[0] > right[0]:
        left, right = right, left
    return left, right


def align_by_eyes(image_bgr: np.ndarray, lm: FaceLandmarks) -> tuple[np.ndarray, float]:
    """Поворачивает изображение так, чтобы линия глаз стала горизонтальной.

    Возвращает (выровненное изображение, угол наклона в градусах).
    """
    h, w = image_bgr.shape[:2]
    left, right = _eye_centers(lm)
    p_left = np.array([left[0] * w, left[1] * h])
    p_right = np.array([right[0] * w, right[1] * h])
    d = p_right - p_left
    angle = math.degrees(math.atan2(d[1], d[0]))

    center = ((p_left + p_right) / 2.0).tolist()
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        image_bgr, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )

    # Поворачиваем сами точки тем же преобразованием.
    pts = lm.points.copy()
    pts_px = np.hstack([pts * np.array([w, h]), np.ones((len(pts), 1))])
    rotated_pts = (matrix @ pts_px.T).T
    lm.points = rotated_pts[:, :2] / np.array([w, h])
    return rotated, angle


def crop_face(
    image_bgr: np.ndarray, lm: FaceLandmarks, margin: float = 0.25
) -> tuple[np.ndarray, FaceLandmarks]:
    """Обрезает изображение вокруг bbox лица с полем margin (доля размера bbox).

    Кроп квадратный: стороны выравниваются по большей стороне bbox. Иначе
    прямоугольник сжимался бы по осям по-разному, а точки нормировались бы
    независимо — углы (gonial, canthal tilt) искажались бы. На тестовом лице
    неравномерный кроп уводил gonial angle с 98.8° на 70.8°.
    """
    h, w = image_bgr.shape[:2]
    xs, ys = lm.points[:, 0] * w, lm.points[:, 1] * h
    x1, x2 = float(xs.min()), float(xs.max())
    y1, y2 = float(ys.min()), float(ys.max())
    bw, bh = x2 - x1, y2 - y1

    # Квадрат со стороной по большей стороне bbox плюс поле.
    side = max(bw, bh) * (1.0 + 2.0 * margin)
    ccx, ccy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    cx1 = round(ccx - side / 2.0)
    cy1 = round(ccy - side / 2.0)
    cx2 = round(ccx + side / 2.0)
    cy2 = round(ccy + side / 2.0)

    # Сдвигаем квадрат внутрь кадра, сохраняя его размер (масштаб осей равен).
    if cx1 < 0:
        cx2 -= cx1
        cx1 = 0
    if cy1 < 0:
        cy2 -= cy1
        cy1 = 0
    if cx2 > w:
        cx1 -= cx2 - w
        cx2 = w
    if cy2 > h:
        cy1 -= cy2 - h
        cy2 = h
    cx1, cy1 = max(0, cx1), max(0, cy1)
    if cx2 - cx1 < 10 or cy2 - cy1 < 10:
        return image_bgr, lm

    cropped = image_bgr[cy1:cy2, cx1:cx2].copy()
    ch, cw = cropped.shape[:2]
    pts = lm.points.copy()
    pts[:, 0] = (pts[:, 0] * w - cx1) / cw
    pts[:, 1] = (pts[:, 1] * h - cy1) / ch
    lm.points = pts
    return cropped, lm


def sharpness_score(image_bgr: np.ndarray) -> float:
    """Дисперсия лапласиана — стандартная мера резкости (Peilkan et al.)."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def estimate_quality(
    image_bgr: np.ndarray, lm: FaceLandmarks, eye_angle: float
) -> tuple[float, list[str]]:
    """Оценивает пригодность фото. Возвращает (0..1, список предупреждений)."""
    h, w = image_bgr.shape[:2]
    warnings: list[str] = []

    xs, ys = lm.points[:, 0] * w, lm.points[:, 1] * h
    face_w = float(xs.max() - xs.min())
    face_h = float(ys.max() - ys.min())

    score = 1.0

    if min(w, h) < 256:
        warnings.append("Низкое разрешение изображения")
        score -= 0.2
    if face_w < MIN_FACE_SIZE_PX:
        warnings.append("Лицо слишком мелкое на фото")
        score -= 0.35

    sharp = sharpness_score(image_bgr)
    if sharp < MIN_SHARPNESS:
        warnings.append("Фото размытое")
        score -= 0.3

    if abs(eye_angle) > MAX_HEAD_TILT_DEG:
        warnings.append("Сильный наклон головы")
        score -= 0.2

    # Лицо должно занимать разумную долю кадра.
    area_ratio = (face_w * face_h) / float(w * h)
    if area_ratio < 0.03:
        warnings.append("Лицо занимает малую часть кадра")
        score -= 0.15

    # Яркость/контраст.
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    mean_brightness = float(gray.mean())
    if mean_brightness < 45 or mean_brightness > 225:
        warnings.append("Проблемы с освещением")
        score -= 0.15

    return max(0.0, min(1.0, score)), warnings


def preprocess(image_bgr: np.ndarray, auto_align: bool = True) -> PreprocessResult:
    """Полный конвейер: landmarks → выравнивание → кроп → качество."""
    lm = extract_landmarks(image_bgr)

    eye_angle = 0.0
    is_aligned = False
    if auto_align:
        image_bgr, eye_angle = align_by_eyes(image_bgr, lm)
        is_aligned = True

    image_bgr, lm = crop_face(image_bgr, lm)
    quality, warnings = estimate_quality(image_bgr, lm, eye_angle)

    return PreprocessResult(
        image_bgr=image_bgr,
        landmarks=lm,
        quality_score=quality,
        is_aligned=is_aligned,
        warnings=warnings,
        eye_angle_deg=eye_angle,
    )
