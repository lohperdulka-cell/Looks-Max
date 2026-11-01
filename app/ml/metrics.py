"""Вычисление сырых метрик лица из landmarks.

Каждая функция возвращает «сырое» значение (угол, отношение, коэффициент) в
антропометрических единицах. Сравнение с эталоном и перевод в 0..10 — в scoring.py.

Все координаты нормализованы (0..1) относительно ширины/высоты изображения.
Для устранения влияния кропа/масштаба расстояния нормируются на ширину лица
(межгониальный или межскуловой размер) либо на высоту лица.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from app.ml.landmarks import FaceLandmarks


@dataclass(slots=True)
class RawMetrics:
    """Сырые значения метрик (ключ -> значение) и вспомогательные величины."""

    values: dict[str, float] = field(default_factory=dict)
    auxiliary: dict[str, float] = field(default_factory=dict)

    def set(self, key: str, value: float) -> None:
        if math.isfinite(value):
            self.values[key] = float(value)


# --- Геометрические хелперы ---------------------------------------------------


def _dist(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b))


def _angle(a: np.ndarray, vertex: np.ndarray, b: np.ndarray) -> float:
    """Угол ABC (в градусах) при вершине vertex."""
    v1 = a - vertex
    v2 = b - vertex
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return float("nan")
    cos = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
    return math.degrees(math.acos(cos))


def _tilt_deg(inner: np.ndarray, outer: np.ndarray) -> float:
    """Наклон линии уголков глаза/брови в градусах, положительный — наружный выше.

    Направление по x нормализуется: у левого глаза наружный уголок левее
    внутреннего, у правого — правее. Без нормализации левая и правая стороны
    дают ~180° и ~0°, и среднее уходит в ~90°.
    """
    # Берём модуль по x: у правого глаза наружный уголок левее внутреннего,
    # у левого — правее. Знак вертикального смещения при этом сохраняется,
    # поэтому наклон остаётся «наружный выше → положительный» для обеих сторон.
    dy = float(outer[1] - inner[1])
    dx = abs(float(outer[0] - inner[0]))
    if dx < 1e-9:
        return 0.0
    return math.degrees(math.atan2(-dy, dx))


def _ramus_point(gonion: np.ndarray) -> np.ndarray:
    """Точка на ветви челюсти: вверх от угла, чуть внутрь (к уху).

    Ветвь почти вертикальна, поэтому смещение вверх с малым наклоном внутрь
    даёт устойчивую оценку gonial angle без отдельной landmark-точки.
    """
    return np.asarray([gonion[0] + 0.02 * (0.5 - gonion[0]), gonion[1] - 0.12])


def _face_width(pts: np.ndarray) -> float:
    return float(pts[:, 0].max() - pts[:, 0].min())


def _face_height(pts: np.ndarray) -> float:
    return float(pts[:, 1].max() - pts[:, 1].min())


# --- Индексы для MediaPipe (468 точек) ---------------------------------------
MP_LEFT_EYE_INNER, MP_LEFT_EYE_OUTER = 133, 33
MP_RIGHT_EYE_INNER, MP_RIGHT_EYE_OUTER = 362, 263
MP_LEFT_BROW_INNER, MP_LEFT_BROW_OUTER = 107, 46
MP_RIGHT_BROW_INNER, MP_RIGHT_BROW_OUTER = 336, 276
MP_NOSE_TIP = 1
MP_NOSE_LEFT, MP_NOSE_RIGHT = 98, 327
MP_MOUTH_LEFT, MP_MOUTH_RIGHT = 61, 291
MP_UPPER_LIP, MP_LOWER_LIP = 13, 14
MP_CHIN = 152
MP_FOREHEAD_TOP = 10
MP_JAW_LEFT, MP_JAW_RIGHT = 172, 397
MP_ZYGOMATIC_LEFT, MP_ZYGOMATIC_RIGHT = 234, 454


def _index_map(lm: FaceLandmarks) -> dict[str, int | tuple[int, int]]:
    """Возвращает карту ключевых точек под конкретную схему landmarks.

    Схема определяется числом точек, а не только провайдером: вручную
    отредактированные (provider="manual") точки сохраняют исходную плотность,
    поэтому опираться на провайдер недостаточно.
    """
    n = len(lm.points)
    if n >= 468:
        return _mediapipe_index_map()
    if n >= 106:
        return _insightface_index_map()
    return _legacy_index_map()


def _insightface_index_map() -> dict[str, int | tuple[int, int]]:
    """Карта точек для InsightFace 2d106det (106 точек)."""
    return {
        "left_eye_inner": 35,
        "left_eye_outer": 33,
        "right_eye_inner": 89,
        "right_eye_outer": 87,
        "left_brow_inner": 43,
        "left_brow_outer": 46,
        "right_brow_inner": 97,
        "right_brow_outer": 100,
        "nose_tip": 86,
        "nose_left": 74,
        "nose_right": 98,
        "mouth_left": 60,
        "mouth_right": 105,
        "upper_lip": 62,
        "lower_lip": 66,
        "chin": 16,
        "forehead_top": 27,
        "jaw_left": 1,
        "jaw_right": 15,
        "zygo_left": 2,
        "zygo_right": 14,
    }


def _mediapipe_index_map() -> dict[str, int | tuple[int, int]]:
    """Карта точек для MediaPipe FaceMesh (468/478 точек)."""
    return {
        "left_eye_inner": MP_LEFT_EYE_INNER,
        "left_eye_outer": MP_LEFT_EYE_OUTER,
        "right_eye_inner": MP_RIGHT_EYE_INNER,
        "right_eye_outer": MP_RIGHT_EYE_OUTER,
        "left_brow_inner": MP_LEFT_BROW_INNER,
        "left_brow_outer": MP_LEFT_BROW_OUTER,
        "right_brow_inner": MP_RIGHT_BROW_INNER,
        "right_brow_outer": MP_RIGHT_BROW_OUTER,
        "nose_tip": MP_NOSE_TIP,
        "nose_left": MP_NOSE_LEFT,
        "nose_right": MP_NOSE_RIGHT,
        "mouth_left": MP_MOUTH_LEFT,
        "mouth_right": MP_MOUTH_RIGHT,
        "upper_lip": MP_UPPER_LIP,
        "lower_lip": MP_LOWER_LIP,
        "chin": MP_CHIN,
        "forehead_top": MP_FOREHEAD_TOP,
        "jaw_left": MP_JAW_LEFT,
        "jaw_right": MP_JAW_RIGHT,
        "zygo_left": MP_ZYGOMATIC_LEFT,
        "zygo_right": MP_ZYGOMATIC_RIGHT,
    }


def _legacy_index_map() -> dict[str, int | tuple[int, int]]:
    """Карта для 68-точечной схемы (dlib-подобной).

    У 68-точечной схемы нет точки лба, поэтому forehead_top приближён
    переносицей (nasion, индекс 27). Ветка используется только как последний
    резерв, когда провайдер вернул нестандартное число точек.
    """
    return {
        "left_eye_inner": 39,
        "left_eye_outer": 36,
        "right_eye_inner": 42,
        "right_eye_outer": 45,
        "left_brow_inner": 21,
        "left_brow_outer": 17,
        "right_brow_inner": 22,
        "right_brow_outer": 26,
        "nose_tip": 30,
        "nose_left": 31,
        "nose_right": 35,
        "mouth_left": 48,
        "mouth_right": 54,
        "upper_lip": 51,
        "lower_lip": 57,
        "chin": 8,
        "forehead_top": 27,
        "jaw_left": 0,
        "jaw_right": 16,
        "zygo_left": 1,
        "zygo_right": 15,
    }


def _p(pts: np.ndarray, idx: int | tuple[int, int]) -> np.ndarray:
    """Возвращает точку (или среднее двух) по индексу карты."""
    if isinstance(idx, tuple):
        return pts[list(idx)].mean(axis=0)
    return pts[int(idx)]


def _mirror_pairs(pts: np.ndarray) -> list[tuple[int, int]]:
    """Пары зеркально-симметричных индексов (для оценки симметрии).

    Используем точки, у которых есть однозначный контрагент по обе стороны оси.
    Для InsightFace 106 и MediaPipe 468 наборы различаются.
    """
    if len(pts) >= 468:
        # FaceMesh: берём репрезентативные пары по обе стороны от оси симметрии.
        return [
            (33, 263),
            (133, 362),
            (159, 386),
            (145, 374),
            (46, 276),
            (53, 283),
            (70, 300),
            (63, 293),
            (61, 291),
            (98, 327),
            (172, 397),
            (234, 454),
            (132, 361),
            (58, 288),
            (136, 365),
            (150, 379),
        ]
    if len(pts) >= 106:
        return [
            (33, 87),
            (35, 89),
            (43, 97),
            (46, 100),
            (60, 105),
            (74, 98),
            (1, 15),
            (2, 14),
            (52, 104),
            (55, 102),
            (56, 103),
        ]
    # Минимальный набор для 68-точечной схемы (dlib-подобной).
    return [(36, 45), (39, 42), (31, 35), (48, 54), (2, 14), (0, 16)]


def compute_raw_metrics(lm: FaceLandmarks, gender: str | None = None) -> RawMetrics:
    """Считает все сырые метрики из landmarks.

    gender используется только как подсказка для вспомогательных величин;
    выбор эталона происходит в scoring.py.
    """
    pts = lm.points
    idx = _index_map(lm)
    out = RawMetrics()

    face_w = _face_width(pts)
    face_h = _face_height(pts)
    if face_w <= 1e-6 or face_h <= 1e-6:
        return out

    out.auxiliary["face_width"] = face_w
    out.auxiliary["face_height"] = face_h
    out.auxiliary["aspect_ratio"] = face_h / face_w

    left_eye_inner = _p(pts, idx["left_eye_inner"])
    left_eye_outer = _p(pts, idx["left_eye_outer"])
    right_eye_inner = _p(pts, idx["right_eye_inner"])
    right_eye_outer = _p(pts, idx["right_eye_outer"])
    left_brow_inner = _p(pts, idx["left_brow_inner"])
    left_brow_outer = _p(pts, idx["left_brow_outer"])
    right_brow_inner = _p(pts, idx["right_brow_inner"])
    right_brow_outer = _p(pts, idx["right_brow_outer"])
    nose_tip = _p(pts, idx["nose_tip"])
    nose_left = _p(pts, idx["nose_left"])
    nose_right = _p(pts, idx["nose_right"])
    mouth_left = _p(pts, idx["mouth_left"])
    mouth_right = _p(pts, idx["mouth_right"])
    upper_lip = _p(pts, idx["upper_lip"])
    lower_lip = _p(pts, idx["lower_lip"])
    chin = _p(pts, idx["chin"])
    forehead_top = _p(pts, idx["forehead_top"])
    jaw_left = _p(pts, idx["jaw_left"])
    jaw_right = _p(pts, idx["jaw_right"])

    # --- Симметрия: среднее относительное отклонение зеркальных пар от оси.
    # Ось симметрии — по вертикали лба и подбородка. Кончик носа в формуле
    # давал бы асимметрию при боковом отклонении носа, которое симметрию лица
    # не описывает.
    midline_x = (forehead_top[0] + chin[0]) / 2.0
    deviations: list[float] = []
    for i, j in _mirror_pairs(pts):
        if i >= len(pts) or j >= len(pts):
            continue
        a, b = pts[i], pts[j]
        da = abs(a[0] - midline_x)
        db = abs(b[0] - midline_x)
        deviations.append(abs(da - db) / face_w)
    if deviations:
        out.set("facial_symmetry", float(np.mean(deviations)))

    # --- Canthal tilt (средний по двум глазам, знак — «вверх к наружному углу»).
    lt = _tilt_deg(left_eye_inner, left_eye_outer)
    rt = _tilt_deg(right_eye_inner, right_eye_outer)
    out.set("canthal_tilt", (lt + rt) / 2.0)

    # --- Трети лица: трихион→бровь→субназале→ментон.
    brow_mid_y = float((left_brow_inner[1] + right_brow_inner[1]) / 2.0)
    subnasale_y = float((nose_left[1] + nose_right[1]) / 2.0)
    trichion_y = float(forehead_top[1])
    menton_y = float(chin[1])
    total = menton_y - trichion_y
    if total > 1e-6:
        t1 = brow_mid_y - trichion_y
        t2 = subnasale_y - brow_mid_y
        t3 = menton_y - subnasale_y
        thirds = np.array([t1, t2, t3], dtype=float)
        thirds = np.clip(thirds, 1e-6, None)
        # 1.0 = идеально равные трети; меньше — при разбросе.
        out.set("facial_thirds", float(thirds.min() / thirds.max()))
        out.auxiliary["third_upper"] = float(t1 / total)
        out.auxiliary["third_middle"] = float(t2 / total)
        out.auxiliary["third_lower"] = float(t3 / total)
        out.auxiliary["lower_upper_face_ratio"] = float(t3 / max(t2, 1e-6))

    # --- Правило пятых: ширина лица делится на 5 равных сегментов.
    eye_w_left = _dist(left_eye_inner, left_eye_outer)
    eye_w_right = _dist(right_eye_inner, right_eye_outer)
    intercanthal = _dist(left_eye_inner, right_eye_inner)
    segments = np.array([eye_w_left, intercanthal, eye_w_right], dtype=float)
    segments = np.clip(segments, 1e-6, None)
    out.set("facial_fifths", float(segments.min() / segments.max()))
    out.set("eye_spacing", float(intercanthal / max((eye_w_left + eye_w_right) / 2.0, 1e-6)))

    # --- Межзрачковое расстояние (IPD) как доля ширины лица.
    left_eye_center = (left_eye_inner + left_eye_outer) / 2.0
    right_eye_center = (right_eye_inner + right_eye_outer) / 2.0
    ipd = _dist(left_eye_center, right_eye_center)
    out.set("interpupillary_ratio", float(ipd / face_w))
    out.auxiliary["ipd"] = ipd

    # --- Наклон брови.
    lb = _tilt_deg(left_brow_inner, left_brow_outer)
    rb = _tilt_deg(right_brow_inner, right_brow_outer)
    out.set("brow_tilt", (lb + rb) / 2.0)

    # --- Пропорции носа: ширина крыльев / высота носа.
    nose_width = _dist(nose_left, nose_right)
    nose_height = abs(float(nose_tip[1] - subnasale_y)) + abs(float(subnasale_y - brow_mid_y))
    if nose_height > 1e-6:
        out.set("nose_proportion", float(nose_width / nose_height))
        out.auxiliary["nose_width_ratio"] = float(nose_width / face_w)

    # --- Пропорции губ.
    mouth_width = _dist(mouth_left, mouth_right)
    lip_height = _dist(upper_lip, lower_lip)
    if lip_height > 1e-6:
        out.set("mouth_proportion", float(mouth_width / lip_height))
        out.auxiliary["mouth_width_ratio"] = float(mouth_width / face_w)

    # --- Филтрум: субназале→верхняя губа относительно нижней трети.
    if total > 1e-6:
        philtrum = abs(float(upper_lip[1] - subnasale_y))
        lower_third = max(menton_y - subnasale_y, 1e-6)
        out.set("philtrum_ratio", float(philtrum / lower_third))

    # --- Gonial angle: угол при гонеоне между ветвью и телом челюсти.
    # Ветвь идёт почти вертикально вверх (к уху), тело — к подбородку.
    # Раньше вторым лучом служило крыло носа: анатомически это не ветвь,
    # и угол выходил ~99° вместо 120-130°.
    gonial_left = _angle(_ramus_point(jaw_left), jaw_left, chin)
    gonial_right = _angle(_ramus_point(jaw_right), jaw_right, chin)
    out.set("gonial_angle", (gonial_left + gonial_right) / 2.0)

    # --- Bigonial width как доля ширины лица.
    bigonial = _dist(jaw_left, jaw_right)
    out.set("bigonial_width", float(bigonial / face_w))
    out.auxiliary["bigonial"] = bigonial

    # --- Угловатость челюсти: насколько углы челюсти выступают относительно щёк.
    cheek_width = _dist(_p(pts, idx["zygo_left"]), _p(pts, idx["zygo_right"]))
    if cheek_width > 1e-6:
        out.set("jaw_angularity", float(bigonial / cheek_width))

    # --- Композитный диморфизм по геометрии (см. scoring для полового эталона).
    brow_to_jaw = _dist(left_brow_outer, jaw_left)
    dimorphism_geom = (bigonial / max(cheek_width, 1e-6)) * (ipd / max(face_w, 1e-6)) / 0.46
    out.set("facial_dimorphism", float(dimorphism_geom))
    out.auxiliary["brow_to_jaw"] = brow_to_jaw

    # --- Отношение нижней трети к средней (для отчёта).
    if "lower_upper_face_ratio" in out.auxiliary:
        out.set("lower_upper_face_ratio", out.auxiliary["lower_upper_face_ratio"])

    # --- Золотое сечение: отношение высоты лица к ширине, нормированное на φ.
    # Классически φ проверяется между вертикальными сегментами; используем
    # отношение высоты лица к ширине скул как устойчивый прокси (см. constants [3],[6]).
    if cheek_width > 1e-6:
        out.set("golden_ratio", float(face_h / cheek_width))

    return out
