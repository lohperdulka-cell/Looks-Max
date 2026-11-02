"""Пошаговая ручная разметка лица (wizard) и интерполяция полного меша.

Зачем нужен ручной режим: автоматические детекторы (InsightFace/MediaPipe)
ошибаются на тенях, очках, наклоне головы и закрытых волосами участках. Ошибка
в 10-15 пикселей на контуре челюсти меняет gonial angle на градусы, а
симметрию — в разы. Поэтому пользователь расставляет 16 ключевых точек, а
остальные 90 получаются деформацией канонического шаблона.

Как работает интерполяция:
  * есть канонический шаблон — 106 точек «идеального» лица (тот же, что в
    демо-провайдере), и позиции 16 ключевых точек на нём;
  * пользователь задаёт новые позиции этих 16 точек;
  * строится RBF-деформация (thin plate spline) с аффинной частью: она
    переводит канонические ключевые точки в заданные, а все остальные точки
    шаблона плавно переносит согласованно. Аффинная часть отвечает за
    глобальный сдвиг/поворот/масштаб, RBF — за локальные поправки;
  * результат — те же 106 точек той же схемы, поэтому весь конвейер метрик
    работает без изменений (схема выбирается по числу точек).

Чистый NumPy: scipy в ядро проекта не входит (тяжёлые детекторы вынесены в
extras), а деплой на Render free собирается из урезанного профиля.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from app.logging_config import get_logger

logger = get_logger(__name__)

# --- Ключевые точки разметки --------------------------------------------------
# Ровно 16 точек: полный набор для симметричного лица. Меньше — не хватает
# опор для челюсти и скул, больше — пользователь устаёт и начинает ошибаться.
# Порядок задаёт последовательность шагов в мастере: сначала глаза, потом нос,
# рот, затем контур (скулы/челюсть/подбородок).
ANCHORS: tuple[dict[str, str], ...] = (
    {
        "key": "left_pupil",
        "label": "Центр левого зрачка",
        "hint": "Смотрите на зрачок, а не на белок. Если глаз прищурен — на середину зрачка.",
    },
    {
        "key": "right_pupil",
        "label": "Центр правого зрачка",
        "hint": "Симметрично левому. Эти две точки задают наклон головы.",
    },
    {
        "key": "left_eye_inner",
        "label": "Внутренний угол левого глаза",
        "hint": "Место, где верхнее и нижнее веко сходятся у носа (слёзный бугорок).",
    },
    {
        "key": "left_eye_outer",
        "label": "Внешний угол левого глаза",
        "hint": "Крайняя точка у виска. Её высота относительно внутреннего угла — canthal tilt.",
    },
    {
        "key": "right_eye_inner",
        "label": "Внутренний угол правого глаза",
        "hint": "Симметрично левому внутреннему углу.",
    },
    {
        "key": "right_eye_outer",
        "label": "Внешний угол правого глаза",
        "hint": "Симметрично левому внешнему углу.",
    },
    {
        "key": "nose_tip",
        "label": "Кончик носа",
        "hint": "Самая выступающая вперёд точка. На анфасе — центр носа внизу.",
    },
    {
        "key": "nose_left",
        "label": "Левое крыло носа",
        "hint": "Самая широкая точка левой ноздри.",
    },
    {
        "key": "nose_right",
        "label": "Правое крыло носа",
        "hint": "Самая широкая точка правой ноздри.",
    },
    {
        "key": "mouth_left",
        "label": "Левый уголок рта",
        "hint": "Место смыкания губ слева. Не берите точку на щеке.",
    },
    {
        "key": "mouth_right",
        "label": "Правый уголок рта",
        "hint": "Место смыкания губ справа.",
    },
    {
        "key": "chin",
        "label": "Низ подбородка",
        "hint": "Нижняя точка подбородка (ментон). Именно она задаёт высоту нижней трети.",
    },
    {
        "key": "left_zygomatic",
        "label": "Левая скула",
        "hint": "Самая выступающая в сторону точка скулы под внешним углом глаза.",
    },
    {
        "key": "right_zygomatic",
        "label": "Правая скула",
        "hint": "Симметрично левой скуле.",
    },
    {
        "key": "left_gonion",
        "label": "Левый угол челюсти",
        "hint": "Изгиб челюсти под ухом. Отсюда считается gonial angle и ширина челюсти.",
    },
    {
        "key": "right_gonion",
        "label": "Правый угол челюсти",
        "hint": "Симметрично левому углу челюсти.",
    },
)

ANCHOR_KEYS: tuple[str, ...] = tuple(a["key"] for a in ANCHORS)
ANCHOR_LABELS: dict[str, str] = {a["key"]: a["label"] for a in ANCHORS}


def _template_points() -> np.ndarray:
    """Канонический 106-точечный шаблон (общий с демо-провайдером)."""
    from app.ml.demo_landmarks import _TEMPLATE

    return np.asarray(_TEMPLATE, dtype=np.float64)


def _canonical_anchors(points: np.ndarray) -> dict[str, np.ndarray]:
    """Позиции 16 ключевых точек на каноническом шаблоне.

    Индексы совпадают с картой InsightFace в metrics.py — держим единый
    источник смысла точек. Гонион и скулы определяются геометрически по
    контуру, как и при автоматической разметке, чтобы ручной режим и
    автодетекция говорили об одних и тех же точках.
    """
    from app.ml import metrics as m

    p = points
    contour = p[m.IF_CONTOUR[0] : m.IF_CONTOUR[1]]
    top_y = float(contour[:, 1].min())
    chin_y = float(p[m.IF_CHIN][1])
    face_h = max(chin_y - top_y, 1e-9)

    band = [
        i
        for i in range(m.IF_CONTOUR[0], m.IF_CONTOUR[1])
        if top_y + 0.58 * face_h < p[i, 1] < top_y + 0.90 * face_h
    ]
    gonion_left = p[min(band, key=lambda i: p[i, 0])] if band else p[m.IF_MOUTH_LEFT]
    gonion_right = p[max(band, key=lambda i: p[i, 0])] if band else p[m.IF_MOUTH_RIGHT]

    # Скулы: самый боковой контур на уровне глаз (та же полоса, что в metrics
    # для jaw_angularity). Иначе скула и угол челюсти указывали бы на одну точку.
    eye_y = float((p[m.IF_LEFT_EYE_OUTER][1] + p[m.IF_RIGHT_EYE_OUTER][1]) / 2.0)
    cheek_band = [i for i in range(len(p)) if abs(p[i, 1] - eye_y) < 0.06 * face_h]
    zygo_left = p[min(cheek_band, key=lambda i: p[i, 0])] if cheek_band else contour[0]
    zygo_right = p[max(cheek_band, key=lambda i: p[i, 0])] if cheek_band else contour[-1]

    left_eye_center = (p[m.IF_LEFT_EYE_INNER] + p[m.IF_LEFT_EYE_OUTER]) / 2.0
    right_eye_center = (p[m.IF_RIGHT_EYE_INNER] + p[m.IF_RIGHT_EYE_OUTER]) / 2.0
    return {
        "left_pupil": left_eye_center,
        "right_pupil": right_eye_center,
        "left_eye_inner": p[m.IF_LEFT_EYE_INNER],
        "left_eye_outer": p[m.IF_LEFT_EYE_OUTER],
        "right_eye_inner": p[m.IF_RIGHT_EYE_INNER],
        "right_eye_outer": p[m.IF_RIGHT_EYE_OUTER],
        "nose_tip": p[m.IF_NOSE_TIP],
        "nose_left": p[m.IF_NOSE_LEFT],
        "nose_right": p[m.IF_NOSE_RIGHT],
        "mouth_left": p[m.IF_MOUTH_LEFT],
        "mouth_right": p[m.IF_MOUTH_RIGHT],
        "chin": p[m.IF_CHIN],
        "left_zygomatic": zygo_left,
        "right_zygomatic": zygo_right,
        "left_gonion": gonion_left,
        "right_gonion": gonion_right,
    }


def canonical_anchor_positions() -> dict[str, list[float]]:
    """Канонические позиции 16 точек (для стартовой подсказки в мастере)."""
    anchors = _canonical_anchors(_template_points())
    return {k: [float(v[0]), float(v[1])] for k, v in anchors.items()}


def detect_anchor_points(landmarks_points: np.ndarray) -> dict[str, list[float]] | None:
    """Извлекает 16 ключевых точек из автоматических landmarks.

    Возвращает None, если схема не 106-точечная: для MediaPipe/68-точечной
    схемы индексы другие, и подставлять их «как есть» нельзя — мастер в этом
    случае начинается с канонического шаблона.
    """
    pts = np.asarray(landmarks_points, dtype=np.float64)
    if len(pts) < 106 or len(pts) >= 468:
        return None
    anchors = _canonical_anchors(pts)
    return {k: [float(v[0]), float(v[1])] for k, v in anchors.items()}


# --- RBF-деформация (thin plate spline) ---------------------------------------


def _tps_kernel(r: np.ndarray) -> np.ndarray:
    """Ядро thin plate spline: r^2 * log(r). В нуле доопределяется нулём."""
    out = np.zeros_like(r)
    mask = r > 1e-12
    out[mask] = (r[mask] ** 2) * np.log(r[mask])
    return out


def _solve_tps(source: np.ndarray, target: np.ndarray, smoothing: float) -> np.ndarray:
    """Решает систему TPS для одной координаты.

    Возвращает вектор коэффициентов [w (n) | a0, ax, ay]. Регуляризация
    smoothing * I делает систему невырожденной даже когда две точки совпали
    (пользователь мог поставить два маркера в одно место) — иначе solve упал бы.
    """
    n = len(source)
    dx = source[:, None, :] - source[None, :, :]
    r = np.sqrt((dx**2).sum(axis=2))
    kernel = _tps_kernel(r)
    kernel[np.diag_indices(n)] += smoothing

    # Аффинная часть: [1, x, y] — глобальный сдвиг, поворот, масштаб.
    affine = np.hstack([np.ones((n, 1)), source])
    system = np.zeros((n + 3, n + 3), dtype=np.float64)
    system[:n, :n] = kernel
    system[:n, n:] = affine
    system[n:, :n] = affine.T

    rhs = np.zeros(n + 3, dtype=np.float64)
    rhs[:n] = target
    return np.linalg.solve(system, rhs)


def _apply_tps(coeffs: np.ndarray, source: np.ndarray, query: np.ndarray) -> np.ndarray:
    """Применяет найденную TPS-функцию к новым точкам."""
    n = len(source)
    dx = query[:, None, :] - source[None, :, :]
    r = np.sqrt((dx**2).sum(axis=2))
    kernel = _tps_kernel(r)
    affine = np.hstack([np.ones((len(query), 1)), query])
    return kernel @ coeffs[:n] + affine @ coeffs[n:]


def interpolate_mesh(
    anchors: Mapping[str, Sequence[float]],
    smoothing: float = 1e-6,
) -> np.ndarray:
    """Строит полный 106-точечный меш по 16 заданным ключевым точкам.

    Точки возвращаются в нормализованных координатах 0..1 — в том же виде, в
    каком их хранит фото, поэтому результат сразу пригоден для метрик.
    """
    missing = [k for k in ANCHOR_KEYS if k not in anchors]
    if missing:
        raise ValueError(f"Не заданы ключевые точки: {', '.join(missing)}")

    template = _template_points()
    canonical = _canonical_anchors(template)

    source = np.asarray([canonical[k] for k in ANCHOR_KEYS], dtype=np.float64)
    target = np.asarray([anchors[k] for k in ANCHOR_KEYS], dtype=np.float64)

    warped = np.empty_like(template)
    for axis in (0, 1):
        coeffs = _solve_tps(source, target[:, axis], smoothing)
        warped[:, axis] = _apply_tps(coeffs, source, template)

    # Клип по кадру: интерполяция может вынести точку за границу изображения,
    # а downstream ждёт нормализованные координаты 0..1.
    warped = np.clip(warped, 0.0, 1.0)
    logger.info(
        "wizard.mesh_interpolated",
        anchors=len(source),
        points=len(warped),
        max_shift=float(np.abs(warped - template).max()),
    )
    return warped


def anchor_definitions(auto: dict[str, list[float]] | None) -> list[dict[str, object]]:
    """Список шагов мастера с подсказками и автопозициями (если есть)."""
    canonical = canonical_anchor_positions()
    out: list[dict[str, object]] = []
    for item in ANCHORS:
        key = item["key"]
        auto_pos = auto.get(key) if auto else None
        out.append(
            {
                "key": key,
                "label": item["label"],
                "hint": item["hint"],
                "canonical": canonical[key],
                "auto": auto_pos,
            }
        )
    return out
