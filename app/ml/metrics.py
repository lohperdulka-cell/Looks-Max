"""Вычисление сырых метрик лица из landmarks.

Все метрики считаются в БЕЗРАЗМЕРНЫХ коэффициентах (пропорциях): отношения
длин, доли ширины/высоты лица, углы в градусах. Пиксельные величины в отчёт не
попадают — иначе результат зависел бы от разрешения снимка.

Ключевые принципы:
  * координаты точек нормализованы (0..1) относительно изображения, поэтому все
    расстояния дополнительно делятся на ширину или высоту лица;
  * перед измерением углов лицо мысленно выпрямляется по линии зрачков (roll),
    иначе наклон головы напрямую искажает canthal tilt и симметрию;
  * каждая метрика проходит проверку на физиологическую допустимость: если
    значение вышло за разумные границы, оно логируется и НЕ попадает в отчёт
    (возвращается None). Это защищает от «63.77» вместо пропорции губ.

Схемы landmark-точек различаются по числу точек и требуют своей карты
семантических индексов: InsightFace 2d106 (106), MediaPipe FaceMesh (468+),
резервная 68-точечная схема. Индексы InsightFace выверены эмпирически (см.
комментарий у _extract_insightface) — в исходной версии они были сдвинуты,
из-за чего, например, углы рта брались из области бровей.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from app.logging_config import get_logger
from app.ml.constants import FOREHEAD_RATIO
from app.ml.landmarks import FaceLandmarks

logger = get_logger(__name__)

# --- Допустимые границы метрик -----------------------------------------------
# Границы намеренно широкие: задача проверки — поймать не «некрасивое» лицо, а
# ошибку единиц измерения (пиксели вместо пропорций, неверные индексы точек).
# Поэтому отсекаются только заведомо невозможные величины: отрицательные длины,
# углы вне 0..180°, отношения порядка десятков. Узкие «эстетические» рамки здесь
# вредны: на шумных landmarks они молча выкидывали метрику из отчёта.
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "facial_symmetry": (0.0, 1.0),
    "canthal_tilt": (-90.0, 90.0),
    "brow_tilt": (-90.0, 90.0),
    "facial_thirds": (0.0, 1.0),
    "facial_fifths": (0.05, 20.0),
    "golden_ratio": (0.05, 20.0),
    "interpupillary_ratio": (0.05, 1.5),
    "eye_spacing": (0.05, 20.0),
    "nose_proportion": (0.02, 20.0),
    "mouth_proportion": (0.05, 30.0),
    "philtrum_ratio": (0.0, 5.0),
    "gonial_angle": (0.0, 180.0),
    "bigonial_width": (0.05, 2.0),
    "jaw_angularity": (0.05, 5.0),
    "facial_dimorphism": (0.02, 10.0),
    "lower_upper_face_ratio": (0.05, 20.0),
}


@dataclass(slots=True)
class RawMetrics:
    """Сырые значения метрик (ключ -> значение) и вспомогательные величины."""

    values: dict[str, float] = field(default_factory=dict)
    auxiliary: dict[str, float] = field(default_factory=dict)

    def set(self, key: str, value: float) -> None:
        """Записывает метрику, если она конечна и физиологически правдоподобна.

        Вне диапазона PLAUSIBLE_RANGES значение отбрасывается с логом: лучше
        пропустить метрику в отчёте, чем показать бессмыслицу.
        """
        if not math.isfinite(value):
            logger.warning("ml.metric.not_finite", metric=key)
            return
        lo, hi = PLAUSIBLE_RANGES.get(key, (float("-inf"), float("inf")))
        if not (lo <= value <= hi):
            logger.warning("ml.metric.implausible", metric=key, value=round(value, 4), lo=lo, hi=hi)
            return
        self.values[key] = float(value)

    def set_aux(self, key: str, value: float) -> None:
        """Записывает вспомогательную величину (для блока «Переменные расчёта»)."""
        if math.isfinite(value):
            self.auxiliary[key] = float(value)


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
    """Наклон линии (уголок→уголок) в градусах; положительный — наружный выше.

    dx берётся по модулю: у левого глаза наружный уголок левее внутреннего, у
    правого — правее. Без этого левая и правая стороны давали бы ~180° и ~0°.
    """
    dy = float(outer[1] - inner[1])
    dx = abs(float(outer[0] - inner[0]))
    if dx < 1e-9:
        return 0.0
    return math.degrees(math.atan2(-dy, dx))


def _roll_deg(eye_left: np.ndarray, eye_right: np.ndarray) -> float:
    """Угол наклона головы (roll) по линии центров глаз, в градусах."""
    return float(math.degrees(math.atan2(eye_right[1] - eye_left[1], eye_right[0] - eye_left[0])))


def _rotate(pts: np.ndarray, origin: np.ndarray, angle_deg: float) -> np.ndarray:
    """Поворачивает точки вокруг origin на -angle (выпрямление наклона головы)."""
    rad = math.radians(-angle_deg)
    c, s = math.cos(rad), math.sin(rad)
    rot = np.array([[c, -s], [s, c]], dtype=np.float64)
    return (pts - origin) @ rot.T + origin


# --- Семантические карты индексов --------------------------------------------

# InsightFace 2d106det. Индексы выверены эмпирически: лицо выпрямлялось по линии
# зрачков, координаты нормировались в bbox, затем для каждого ориентира искался
# индекс по геометрическому правилу и проверялась устойчивость на нескольких
# разных лицах (t1, Grace Hopper, astronaut). Совпавшие на всех лицах значения
# зафиксированы здесь.
#
# Типичная ошибка исходной версии: углы рта брались по индексам 60 и 105 —
# это точки внутренних губ и верхней губы, а не уголки. Реальные уголки — 52 и
# 61. Аналогично «лоб» (27) оказался точкой правой челюсти.
IF_LEFT_EYE_OUTER = 35
IF_LEFT_EYE_INNER = 39
IF_RIGHT_EYE_INNER = 89
IF_RIGHT_EYE_OUTER = 93
IF_LEFT_BROW_OUTER = 43
IF_LEFT_BROW_INNER = 50
# Правая внутренняя бровь — 102, а не 97: 97 лежит выше (y≈0.229 против 0.202)
# и зеркальна точке 46, а не 50. Из-за этого наклоны бровей слева и справа
# считались по разным по высоте точкам и давали ложную асимметрию.
IF_RIGHT_BROW_INNER = 102
IF_RIGHT_BROW_OUTER = 101
IF_NOSE_TIP = 86
IF_NOSE_LEFT = 77
IF_NOSE_RIGHT = 83
IF_SUBNASALE = 80
IF_MOUTH_LEFT = 52
IF_MOUTH_RIGHT = 61
IF_CHIN = 0
IF_CONTOUR = (0, 33)
IF_MOUTH = (52, 72)

# MediaPipe FaceMesh (468/478 точек): стандартная схема Google.
MP_LEFT_EYE_OUTER, MP_LEFT_EYE_INNER = 33, 133
MP_RIGHT_EYE_INNER, MP_RIGHT_EYE_OUTER = 362, 263
MP_LEFT_BROW_OUTER, MP_LEFT_BROW_INNER = 70, 107
MP_RIGHT_BROW_INNER, MP_RIGHT_BROW_OUTER = 336, 300
MP_NOSE_TIP = 1
MP_NOSE_LEFT, MP_NOSE_RIGHT = 98, 327
MP_SUBNASALE = 2
MP_MOUTH_LEFT, MP_MOUTH_RIGHT = 61, 291
MP_CHIN = 152
MP_FOREHEAD_TOP = 10
MP_JAW_LEFT, MP_JAW_RIGHT = 172, 397
MP_ZYGO_LEFT, MP_ZYGO_RIGHT = 234, 454
# Верхняя и нижняя точки контура губ FaceMesh. Индексы 13/14 — внутренние точки
# сомкнутой линии губ; у сомкнутого рта они практически совпадают по y, поэтому
# высота губ получалась нулевой и «пропорция губ» уходила в сотни.
MP_UPPER_LIP, MP_LOWER_LIP = 0, 17


@dataclass(slots=True)
class _PointMap:
    """Семантические точки лица, извлечённые из конкретной схемы landmarks."""

    points: dict[str, np.ndarray]
    contour: np.ndarray
    face_width: float
    face_height: float
    scheme: str
    # Контур лица именно этой схемы. Поле нужно для оси симметрии: индексы
    # 0..32 задают контур только у InsightFace/legacy, у MediaPipe это совсем
    # другие точки, поэтому брать срез массива по «общим» индексам нельзя.
    # Может быть срезом (непрерывный диапазон) или массивом индексов.
    contour_span: slice | np.ndarray = slice(0, 33)


def _extract_insightface(pts: np.ndarray) -> _PointMap:
    """Точки для схемы InsightFace 2d106det.

    Гонион (угол челюсти) и точки губ не имеют фиксированных индексов в
    публичной разметке, поэтому определяются геометрически по контуру лица:
    гонион — самый боковой контур в нижней половине, губы — крайние по вертикали
    точки центральной полосы рта.
    """
    contour = pts[IF_CONTOUR[0] : IF_CONTOUR[1]]
    top_y = float(contour[:, 1].min())
    chin_y = float(pts[IF_CHIN][1])
    face_h = max(chin_y - top_y, 1e-9)
    face_w = float(contour[:, 0].max() - contour[:, 0].min())

    # Гонион (угол челюсти): самый боковой контур в зоне угла челюсти.
    # Зона взята ниже скул (0.58) и выше подбородка (0.90): если брать шире,
    # в «угол челюсти» попадала скула и bigonial выходил почти равным ширине лица.
    band = [
        i
        for i in range(IF_CONTOUR[0], IF_CONTOUR[1])
        if top_y + 0.58 * face_h < pts[i, 1] < top_y + 0.90 * face_h
    ]
    if band:
        gonion_left = pts[min(band, key=lambda i: pts[i, 0])]
        gonion_right = pts[max(band, key=lambda i: pts[i, 0])]
    else:
        gonion_left = pts[IF_MOUTH_LEFT]
        gonion_right = pts[IF_MOUTH_RIGHT]

    # Губы: крайние по вертикали точки центральной полосы рта.
    mouth_cx = (pts[IF_MOUTH_LEFT][0] + pts[IF_MOUTH_RIGHT][0]) / 2.0
    mouth_band = [
        i
        for i in range(IF_MOUTH[0], IF_MOUTH[1])
        if abs(pts[i, 0] - mouth_cx) < 0.22 * max(face_w, 1e-9)
    ]
    if mouth_band:
        upper_lip = pts[min(mouth_band, key=lambda i: pts[i, 1])]
        lower_lip = pts[max(mouth_band, key=lambda i: pts[i, 1])]
    else:
        upper_lip = pts[IF_MOUTH_LEFT]
        lower_lip = pts[IF_MOUTH_RIGHT]

    points = {
        "left_eye_outer": pts[IF_LEFT_EYE_OUTER],
        "left_eye_inner": pts[IF_LEFT_EYE_INNER],
        "right_eye_inner": pts[IF_RIGHT_EYE_INNER],
        "right_eye_outer": pts[IF_RIGHT_EYE_OUTER],
        "left_brow_outer": pts[IF_LEFT_BROW_OUTER],
        "left_brow_inner": pts[IF_LEFT_BROW_INNER],
        "right_brow_inner": pts[IF_RIGHT_BROW_INNER],
        "right_brow_outer": pts[IF_RIGHT_BROW_OUTER],
        "nose_tip": pts[IF_NOSE_TIP],
        "nose_left": pts[IF_NOSE_LEFT],
        "nose_right": pts[IF_NOSE_RIGHT],
        "subnasale": pts[IF_SUBNASALE],
        "mouth_left": pts[IF_MOUTH_LEFT],
        "mouth_right": pts[IF_MOUTH_RIGHT],
        "upper_lip": upper_lip,
        "lower_lip": lower_lip,
        "chin": pts[IF_CHIN],
        "gonion_left": gonion_left,
        "gonion_right": gonion_right,
    }
    return _PointMap(
        points=points, contour=contour, face_width=face_w, face_height=face_h, scheme="insightface"
    )


def _extract_mediapipe(pts: np.ndarray) -> _PointMap:
    """Точки для схемы MediaPipe FaceMesh (468/478)."""
    contour_idx = [
        10,
        338,
        297,
        332,
        284,
        251,
        389,
        356,
        454,
        323,
        361,
        288,
        397,
        365,
        379,
        378,
        400,
        377,
        152,
        148,
        176,
        149,
        150,
        136,
        172,
        58,
        132,
        93,
        234,
        127,
        162,
        21,
        54,
        103,
        67,
        109,
    ]
    span = np.asarray(contour_idx)
    contour = pts[span]
    top_y = float(contour[:, 1].min())
    chin_y = float(pts[MP_CHIN][1])
    face_h = max(chin_y - top_y, 1e-9)
    face_w = float(contour[:, 0].max() - contour[:, 0].min())

    points = {
        "left_eye_outer": pts[MP_LEFT_EYE_OUTER],
        "left_eye_inner": pts[MP_LEFT_EYE_INNER],
        "right_eye_inner": pts[MP_RIGHT_EYE_INNER],
        "right_eye_outer": pts[MP_RIGHT_EYE_OUTER],
        "left_brow_outer": pts[MP_LEFT_BROW_OUTER],
        "left_brow_inner": pts[MP_LEFT_BROW_INNER],
        "right_brow_inner": pts[MP_RIGHT_BROW_INNER],
        "right_brow_outer": pts[MP_RIGHT_BROW_OUTER],
        "nose_tip": pts[MP_NOSE_TIP],
        "nose_left": pts[MP_NOSE_LEFT],
        "nose_right": pts[MP_NOSE_RIGHT],
        "subnasale": pts[MP_SUBNASALE],
        "mouth_left": pts[MP_MOUTH_LEFT],
        "mouth_right": pts[MP_MOUTH_RIGHT],
        "upper_lip": pts[MP_UPPER_LIP],
        "lower_lip": pts[MP_LOWER_LIP],
        "chin": pts[MP_CHIN],
        "gonion_left": pts[MP_JAW_LEFT],
        "gonion_right": pts[MP_JAW_RIGHT],
    }
    return _PointMap(
        points=points,
        contour=contour,
        face_width=face_w,
        face_height=face_h,
        scheme="mediapipe",
        contour_span=span,
    )


def _extract_legacy(pts: np.ndarray) -> _PointMap:
    """Резервная 68-точечная схема (dlib-подобная).

    У неё нет точки лба, поэтому верхняя граница лица приближена переносицей (27).
    Ветка используется только если провайдер вернул нестандартное число точек.
    """
    contour = pts[0:17]
    face_w = float(contour[:, 0].max() - contour[:, 0].min())
    face_h = float(pts[8][1] - pts[27][1])
    points = {
        "left_eye_outer": pts[36],
        "left_eye_inner": pts[39],
        "right_eye_inner": pts[42],
        "right_eye_outer": pts[45],
        "left_brow_outer": pts[17],
        "left_brow_inner": pts[21],
        "right_brow_inner": pts[22],
        "right_brow_outer": pts[26],
        "nose_tip": pts[30],
        "nose_left": pts[31],
        "nose_right": pts[35],
        "subnasale": pts[33],
        "mouth_left": pts[48],
        "mouth_right": pts[54],
        "upper_lip": pts[51],
        "lower_lip": pts[57],
        "chin": pts[8],
        "gonion_left": pts[4],
        "gonion_right": pts[12],
    }
    return _PointMap(
        points=points, contour=contour, face_width=face_w, face_height=face_h, scheme="legacy"
    )


def _extract_points(pts: np.ndarray) -> _PointMap:
    """Выбирает карту точек по размеру схемы (не по имени провайдера)."""
    n = len(pts)
    if n >= 468:
        return _extract_mediapipe(pts)
    if n >= 106:
        return _extract_insightface(pts)
    return _extract_legacy(pts)


def _legacy_index_map() -> dict[str, int]:
    """Карта резервной 68-точечной схемы (нужна для совместимости и тестов)."""
    return {
        "left_eye_outer": 36,
        "left_eye_inner": 39,
        "right_eye_inner": 42,
        "right_eye_outer": 45,
        "left_brow_outer": 17,
        "left_brow_inner": 21,
        "right_brow_inner": 22,
        "right_brow_outer": 26,
        "nose_tip": 30,
        "nose_left": 31,
        "nose_right": 35,
        "subnasale": 33,
        "mouth_left": 48,
        "mouth_right": 54,
        "upper_lip": 51,
        "lower_lip": 57,
        "chin": 8,
        "gonion_left": 4,
        "gonion_right": 12,
    }


def _index_map(lm: FaceLandmarks) -> dict[str, int]:
    """Плоская карта «семантическое имя -> индекс» для схемы landmarks.

    Нужна там, где важны именно номера точек (разметка поверх фото, ручная
    правка), а не геометрия. Схема выбирается по числу точек, поэтому ручные
    точки (provider="manual") с той же плотностью получают ту же карту.
    """
    n = len(lm.points)
    if n >= 468:
        return {
            "left_eye_outer": MP_LEFT_EYE_OUTER,
            "left_eye_inner": MP_LEFT_EYE_INNER,
            "right_eye_inner": MP_RIGHT_EYE_INNER,
            "right_eye_outer": MP_RIGHT_EYE_OUTER,
            "left_brow_outer": MP_LEFT_BROW_OUTER,
            "left_brow_inner": MP_LEFT_BROW_INNER,
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
            "gonion_left": MP_JAW_LEFT,
            "gonion_right": MP_JAW_RIGHT,
        }
    if n >= 106:
        return {
            "left_eye_outer": IF_LEFT_EYE_OUTER,
            "left_eye_inner": IF_LEFT_EYE_INNER,
            "right_eye_inner": IF_RIGHT_EYE_INNER,
            "right_eye_outer": IF_RIGHT_EYE_OUTER,
            "left_brow_outer": IF_LEFT_BROW_OUTER,
            "left_brow_inner": IF_LEFT_BROW_INNER,
            "right_brow_inner": IF_RIGHT_BROW_INNER,
            "right_brow_outer": IF_RIGHT_BROW_OUTER,
            "nose_tip": IF_NOSE_TIP,
            "nose_left": IF_NOSE_LEFT,
            "nose_right": IF_NOSE_RIGHT,
            "subnasale": IF_SUBNASALE,
            "mouth_left": IF_MOUTH_LEFT,
            "mouth_right": IF_MOUTH_RIGHT,
            "chin": IF_CHIN,
        }
    return {
        "left_eye_outer": 36,
        "left_eye_inner": 39,
        "right_eye_inner": 42,
        "right_eye_outer": 45,
        "left_brow_outer": 17,
        "left_brow_inner": 21,
        "right_brow_inner": 22,
        "right_brow_outer": 26,
        "nose_tip": 30,
        "nose_left": 31,
        "nose_right": 35,
        "subnasale": 33,
        "mouth_left": 48,
        "mouth_right": 54,
        "upper_lip": 51,
        "lower_lip": 57,
        "chin": 8,
        "gonion_left": 4,
        "gonion_right": 12,
    }


def _mirror_pairs(scheme: str) -> list[tuple[int, int]]:
    """Пары зеркально-симметричных индексов для оценки симметрии.

    Пары подобраны так, чтобы точки были анатомическими контрагентами
    (левый↔правый), а не просто «примерно напротив».
    """
    if scheme == "mediapipe":
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
    if scheme == "insightface":
        # Пары выверены как истинная инволюция отражения: для каждого индекса
        # бралась ближайшая зеркальная точка (задача о назначениях), и пара
        # принималась только если она взаимна. Произвольные «похожие» пары
        # (например, 36↔90 вместо 36↔91) давали ложную асимметрию.
        return [
            (1, 17),
            (2, 18),
            (3, 19),
            (4, 20),
            (5, 21),
            (6, 22),
            (7, 23),
            (8, 24),
            (9, 25),
            (10, 26),
            (11, 27),
            (12, 28),
            (13, 29),
            (14, 30),
            (15, 31),
            (16, 32),
            (33, 87),
            (34, 88),
            (35, 93),
            (36, 91),
            (37, 90),
            (38, 94),
            (39, 89),
            (40, 92),
            (41, 96),
            (42, 95),
            (43, 101),
            (44, 100),
            (45, 99),
            (46, 97),
            (47, 98),
            (48, 105),
            (49, 104),
            (50, 102),
            (51, 103),
            (52, 61),
            (54, 57),
            (55, 58),
            (56, 59),
            (63, 67),
            (64, 68),
            (65, 69),
            (66, 70),
            (75, 81),
            (76, 82),
            (77, 83),
            (78, 84),
            (79, 85),
        ]
    return [(36, 45), (39, 42), (31, 35), (48, 54), (2, 14), (0, 16)]


def _trichion(pmap: _PointMap, lm: FaceLandmarks) -> np.ndarray:
    """Оценка точки трихион (граница роста волос) — верхняя граница лица.

    Схема InsightFace 106 не содержит точки лба, поэтому трихион оценивается по
    верхней границе bbox детектора: SCRFD включает лоб в рамку лица. Если bbox
    отсутствует, лоб достраивается над верхом контура на ту же долю высоты лица
    (FOREHEAD_RATIO): иначе верхняя треть считалась бы от уровня ушей и трети
    выходили бы нулевыми.

    bbox хранится в нормализованных координатах, а контур здесь — в физических,
    поэтому bbox приводится к тому же масштабу.
    """
    contour_top_y = float(pmap.contour[:, 1].min())
    contour_mid_x = float(pmap.contour[:, 0].mean())
    chin_y = float(pmap.points["chin"][1])
    face_h = max(chin_y - contour_top_y, 1e-6)
    fallback = np.array([contour_mid_x, contour_top_y - FOREHEAD_RATIO * face_h])
    if lm.bbox is not None:
        w, h = lm.image_size if lm.image_size is not None else (1000, 1000)
        bbox_top_y = float(lm.bbox[1]) * h
        if bbox_top_y < contour_top_y:
            bbox_mid_x = float((lm.bbox[0] + lm.bbox[2]) / 2.0) * w
            return np.array([bbox_mid_x, bbox_top_y])
    return fallback


def compute_raw_metrics(lm: FaceLandmarks, gender: str | None = None) -> RawMetrics:
    """Считает все сырые метрики из landmarks.

    gender используется только как подсказка для вспомогательных величин;
    выбор эталона происходит в scoring.py.
    """
    pts = np.asarray(lm.points, dtype=np.float64)
    out = RawMetrics()
    if len(pts) < 5:
        return out

    # Точки нормализованы по ширине и высоте НЕЗАВИСИМО, поэтому в исходном виде
    # расстояния и углы искажены пропорцией кадра (для 1280×886 — в 1.44 раза).
    # Переводим в физическую систему: умножаем x на ширину, y на высоту. Если
    # размер неизвестен, считаем оси равными (квадратное изображение).
    if lm.image_size is not None:
        w, h = lm.image_size
    else:
        w = h = 1000
    pts = pts * np.array([float(w), float(h)], dtype=np.float64)

    pmap = _extract_points(pts)
    if pmap.face_width <= 1e-6 or pmap.face_height <= 1e-6:
        logger.warning("ml.metrics.degenerate_bbox", w=pmap.face_width, h=pmap.face_height)
        return out

    face_w = pmap.face_width
    face_h = pmap.face_height
    out.set_aux("face_width", face_w)
    out.set_aux("face_height", face_h)
    out.set_aux("aspect_ratio", face_h / face_w)

    p = pmap.points
    left_eye_inner = p["left_eye_inner"]
    left_eye_outer = p["left_eye_outer"]
    right_eye_inner = p["right_eye_inner"]
    right_eye_outer = p["right_eye_outer"]

    # --- Выпрямление наклона головы (roll) по линии зрачков.
    # Без этого canthal tilt и симметрия содержат систематическую ошибку от
    # наклона камеры/головы.
    left_eye_center = (left_eye_inner + left_eye_outer) / 2.0
    right_eye_center = (right_eye_inner + right_eye_outer) / 2.0
    roll = _roll_deg(left_eye_center, right_eye_center)
    origin = (left_eye_center + right_eye_center) / 2.0
    rp = _rotate(pts, origin, roll)
    r = {k: _rotate(v[None, :], origin, roll)[0] for k, v in p.items()}
    trichion = _rotate(_trichion(pmap, lm)[None, :], origin, roll)[0]

    # Лицо должно остаться «головой вверх». Если после поворота подбородок
    # оказался выше трихиона, линия глаз задана в обратном порядке (или точки
    # бессмысленны) — доворачиваем на 180°. На нормальных анфасах ветка не
    # срабатывает: roll там мал, и подбородок заведомо ниже лба.
    if float(r["chin"][1]) < float(trichion[1]):
        roll += 180.0
        rp = _rotate(pts, origin, roll)
        r = {k: _rotate(v[None, :], origin, roll)[0] for k, v in p.items()}
        trichion = _rotate(_trichion(pmap, lm)[None, :], origin, roll)[0]
    out.set_aux("head_roll_deg", roll)

    # --- Симметрия: среднее относительное отклонение зеркальных пар от оси.
    # Ось — вертикаль через середину между внутренними углами глаз и центром
    # контура. Кончик носа в оси НЕ участвует: он бывает смещён и тогда
    # «создавал» асимметрию на симметричном лице.
    contour_x = rp[pmap.contour_span, 0]
    midline_x = float(
        (
            (r["left_eye_inner"][0] + r["right_eye_inner"][0]) / 2.0
            + (float(contour_x.min()) + float(contour_x.max())) / 2.0
        )
        / 2.0
    )
    deviations: list[float] = []
    for i, j in _mirror_pairs(pmap.scheme):
        if i >= len(rp) or j >= len(rp):
            continue
        a, b = rp[i], rp[j]
        da = abs(a[0] - midline_x)
        db = abs(b[0] - midline_x)
        deviations.append(abs(da - db) / face_w)
    if deviations:
        out.set("facial_symmetry", float(np.mean(deviations)))

    # --- Canthal tilt: средний по двум глазам, положительный — наружный выше.
    lt = _tilt_deg(r["left_eye_inner"], r["left_eye_outer"])
    rt = _tilt_deg(r["right_eye_inner"], r["right_eye_outer"])
    out.set("canthal_tilt", (lt + rt) / 2.0)

    # --- Трети лица: трихион→бровь→субназале→ментон.
    brow_mid_y = float((r["left_brow_inner"][1] + r["right_brow_inner"][1]) / 2.0)
    subnasale_y = float(r["subnasale"][1])
    trichion = _rotate(_trichion(pmap, lm)[None, :], origin, roll)[0]
    trichion_y = float(trichion[1])
    menton_y = float(r["chin"][1])
    total = menton_y - trichion_y
    if total > 1e-6:
        t1 = brow_mid_y - trichion_y
        t2 = subnasale_y - brow_mid_y
        t3 = menton_y - subnasale_y
        thirds = np.clip(np.array([t1, t2, t3], dtype=float), 1e-6, None)
        out.set("facial_thirds", float(thirds.min() / thirds.max()))
        out.set_aux("third_upper", float(t1 / total))
        out.set_aux("third_middle", float(t2 / total))
        out.set_aux("third_lower", float(t3 / total))
        out.set_aux("lower_upper_face_ratio", float(t3 / max(t2, 1e-6)))

    # --- Правило пятых: ширина лица делится на 5 равных сегментов, поэтому
    # ширина глазной щели должна равняться 1/5 ширины лица. Метрика — отношение
    # фактической ширины глаза к идеальной пятой части (эталон 1.0).
    eye_w_left = _dist(r["left_eye_inner"], r["left_eye_outer"])
    eye_w_right = _dist(r["right_eye_inner"], r["right_eye_outer"])
    eye_w_avg = (eye_w_left + eye_w_right) / 2.0
    intercanthal = _dist(r["left_eye_inner"], r["right_eye_inner"])
    out.set("facial_fifths", float(eye_w_avg / max(face_w / 5.0, 1e-6)))
    out.set("eye_spacing", float(intercanthal / max(eye_w_avg, 1e-6)))

    # --- Межзрачковое расстояние (IPD) как доля ширины лица.
    ipd = _dist(left_eye_center, right_eye_center)
    out.set("interpupillary_ratio", float(ipd / face_w))
    out.set_aux("ipd", ipd)

    # --- Наклон брови.
    lb = _tilt_deg(r["left_brow_inner"], r["left_brow_outer"])
    rb = _tilt_deg(r["right_brow_inner"], r["right_brow_outer"])
    out.set("brow_tilt", (lb + rb) / 2.0)

    # --- Пропорции носа: ширина крыльев / высота носа (переносица→субназале).
    nose_width = _dist(r["nose_left"], r["nose_right"])
    nose_height = abs(float(r["subnasale"][1] - brow_mid_y))
    if nose_height > 1e-6:
        out.set("nose_proportion", float(nose_width / nose_height))
        out.set_aux("nose_width_ratio", float(nose_width / face_w))

    # --- Пропорции губ: ширина рта / высота губ.
    mouth_width = _dist(r["mouth_left"], r["mouth_right"])
    lip_height = abs(float(r["upper_lip"][1] - r["lower_lip"][1]))
    if lip_height > 1e-6:
        out.set("mouth_proportion", float(mouth_width / lip_height))
        out.set_aux("mouth_width_ratio", float(mouth_width / face_w))

    # --- Филтрум: субназале→верхняя губа относительно нижней трети.
    if total > 1e-6:
        philtrum = abs(float(r["upper_lip"][1] - subnasale_y))
        lower_third = max(menton_y - subnasale_y, 1e-6)
        out.set("philtrum_ratio", float(philtrum / lower_third))

    # --- Gonial angle: угол при гонионе между ветвью (вверх) и телом (к подбородку).
    # Смещение точки ветви задаётся в долях высоты лица: расчёт идёт в пикселях,
    # и абсолютное смещение 0.12 px было бы нулевым.
    def gonial(gonion: np.ndarray) -> float:
        offset_x = 0.04 * face_h * (0.5 - gonion[0] / max(face_w, 1e-6))
        ramus = np.array([gonion[0] + offset_x, gonion[1] - 0.25 * face_h])
        return _angle(ramus, gonion, r["chin"])

    out.set("gonial_angle", (gonial(r["gonion_left"]) + gonial(r["gonion_right"])) / 2.0)

    # --- Bigonial width как доля ширины лица.
    bigonial = _dist(r["gonion_left"], r["gonion_right"])
    out.set("bigonial_width", float(bigonial / face_w))
    out.set_aux("bigonial", bigonial)

    # --- Угловатость челюсти: насколько углы челюсти выдаются относительно щёк.
    # Ширина щёк берётся по контуру на уровне глаз (скулы), а не по всему лицу:
    # так метрика отражает именно «резкость» линии челюсти.
    cheek_level_y = float((r["left_eye_outer"][1] + r["right_eye_outer"][1]) / 2.0)
    cheek_band = [i for i in range(len(rp)) if abs(rp[i, 1] - cheek_level_y) < 0.06 * face_h]
    cheek_width = float(rp[cheek_band, 0].max() - rp[cheek_band, 0].min()) if cheek_band else face_w
    if cheek_width > 1e-6:
        out.set("jaw_angularity", float(bigonial / cheek_width))

    # --- Композитный диморфизм по геометрии (эталон по полу — в scoring).
    dimorphism_geom = (bigonial / max(cheek_width, 1e-6)) * (ipd / max(face_w, 1e-6)) / 0.46
    out.set("facial_dimorphism", float(dimorphism_geom))

    # --- Отношение нижней трети к средней (для отчёта).
    if "lower_upper_face_ratio" in out.auxiliary:
        out.set("lower_upper_face_ratio", out.auxiliary["lower_upper_face_ratio"])

    # --- Золотое сечение: высота лица к ширине скул.
    if cheek_width > 1e-6:
        out.set("golden_ratio", float(face_h / cheek_width))

    return out
