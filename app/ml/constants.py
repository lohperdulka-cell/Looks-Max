"""Эталоны, веса и научные источники метрик лица.

Здесь собраны все «магические числа» модели в одном месте, каждое — со ссылкой
на источник. Значения являются популяционными ориентирами из антропометрии и
эстетической хирургии; они НЕ являются истиной в последней инстанции и служат
лишь точкой отсчёта для оценки отклонения.

Ключевые источники:
  [1] Naini F.B. «Facial Aesthetics: Concepts and Clinical Diagnosis», Wiley-Blackwell, 2011.
  [2] Farkas L.G. «Anthropometry of the Head and Face», Raven Press, 1994.
  [3] Ricketts R.M. «Divine proportion in facial esthetics», Clin Plast Surg, 1982.
  [4] Powell N., Humphreys B. «Proportions of the Aesthetic Face», Thieme, 1984.
  [5] Bashour M. «History and current concepts in the analysis of facial attractiveness»,
      Plast Reconstr Surg, 2006.
  [6] Pallett P.M., Link S., Lee K. «New golden ratios for facial beauty»,
      Vision Research, 2010.
  [7] Little A.C. et al. «Symmetry, sexual dimorphism and attractiveness»,
      Evolution and Human Behavior, 2011.
  [8] Thornhill R., Gangestad S.W. «Facial sexual dimorphism, developmental stability,
      and parasite infections», Evolution and Human Behavior, 2006.
  [9] Mommaerts M.Y. «The ideal nasal profile: a review», Rhinology, 2010.
  [10] Perrett D.I. et al. «Effects of sexual dimorphism on facial attractiveness»,
      Nature, 1998.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class MetricCategory(StrEnum):
    """Категории метрик для группировки в отчёте."""

    SYMMETRY = "symmetry"
    PROPORTIONS = "proportions"
    JAWLINE = "jawline"
    EYES = "eyes"
    DIMORPHISM = "dimorphism"
    HARMONY = "harmony"


CATEGORY_LABELS: dict[MetricCategory, str] = {
    MetricCategory.SYMMETRY: "Симметрия",
    MetricCategory.PROPORTIONS: "Пропорции",
    MetricCategory.JAWLINE: "Линия челюсти",
    MetricCategory.EYES: "Глаза",
    MetricCategory.DIMORPHISM: "Диморфизм",
    MetricCategory.HARMONY: "Гармония",
}


@dataclass(slots=True)
class MetricSpec:
    """Описание метрики: эталон, допустимое отклонение и вес в агрегате."""

    key: str
    label: str
    category: MetricCategory
    reference: float
    tolerance: float  # насколько отклонение считается «половиной» штрафа
    weight: float
    unit: str = ""
    source: str = ""
    # Для метрик с половыми различиями: эталон отдельно для male/female.
    reference_by_gender: dict[str, float] = field(default_factory=dict)
    notes: str = ""


# --- Определения метрик -------------------------------------------------------
# tolerance подобраны так, чтобы отклонение = tolerance давало ~5/10, а
# выход за 2*tolerance — оценку ниже 2.5/10 (линейно-логистическая шкала).
METRIC_SPECS: tuple[MetricSpec, ...] = (
    MetricSpec(
        key="facial_symmetry",
        label="Симметрия лица",
        category=MetricCategory.SYMMETRY,
        reference=0.0,
        tolerance=0.030,
        weight=1.4,
        unit="доля ширины лица",
        source="[1],[7] — билатеральная симметрия положительно коррелирует с привлекательностью",
        notes="Отклонение зеркальных landmark-пар от средней оси, нормировано на ширину лица.",
    ),
    MetricSpec(
        key="canthal_tilt",
        label="Canthal tilt",
        category=MetricCategory.EYES,
        reference=6.0,  # положительный наклон наружного угла глаза
        tolerance=4.0,
        weight=1.1,
        unit="°",
        source="[4],[5] — позитивный canthal tilt ассоциируется с привлекательностью",
        reference_by_gender={"male": 5.0, "female": 7.0},
        notes="Угол между внутренним и наружным углами глаза относительно горизонтали.",
    ),
    MetricSpec(
        key="facial_thirds",
        label="Баланс третей лица",
        category=MetricCategory.PROPORTIONS,
        reference=1.0,
        tolerance=0.15,
        weight=1.2,
        unit="коэф. равномерности",
        source="[2],[4] — равные трети (трихион–глабелла–субназале–ментон)",
        notes="1.0 = идеально равные трети; <1 при доминировании одной из третей.",
    ),
    MetricSpec(
        key="facial_fifths",
        label="Правило пятых",
        category=MetricCategory.PROPORTIONS,
        reference=1.0,
        tolerance=0.12,
        weight=1.0,
        unit="коэф. равномерности",
        source="[4] — ширина лица делится на 5 примерно равных сегментов",
    ),
    MetricSpec(
        key="golden_ratio",
        label="Золотое сечение",
        category=MetricCategory.PROPORTIONS,
        reference=1.618,
        tolerance=0.18,
        weight=1.0,
        unit="φ",
        source="[3],[6] — φ≈1.618 в вертикальных пропорциях лица",
    ),
    MetricSpec(
        key="interpupillary_ratio",
        label="Межзрачковое расстояние",
        category=MetricCategory.EYES,
        reference=0.46,
        tolerance=0.05,
        weight=0.9,
        unit="доля ширины лица",
        source="[2] — IPD ≈ 46% ширины лица в норме",
    ),
    MetricSpec(
        key="eye_spacing",
        label="Расстояние между глазами",
        category=MetricCategory.EYES,
        reference=1.0,
        tolerance=0.18,
        weight=0.9,
        unit="внутренний/наружный угол",
        source="[4] — межглазная щель ≈ ширине глаза",
    ),
    MetricSpec(
        key="brow_tilt",
        label="Наклон брови",
        category=MetricCategory.EYES,
        reference=8.0,
        tolerance=6.0,
        weight=0.7,
        unit="°",
        source="[5] — лёгкий позитивный наклон брови",
    ),
    MetricSpec(
        key="nose_proportion",
        label="Пропорции носа",
        category=MetricCategory.PROPORTIONS,
        reference=1.0,
        tolerance=0.20,
        weight=0.9,
        unit="ширина/высота",
        source="[9] — ширина крыльев ≈ 70-80% высоты носа",
    ),
    MetricSpec(
        key="mouth_proportion",
        label="Пропорции губ",
        category=MetricCategory.PROPORTIONS,
        reference=1.6,
        tolerance=0.35,
        weight=0.8,
        unit="ширина/высота губ",
        source="[4],[5] — соотношение ширины рта и высоты губ",
    ),
    MetricSpec(
        key="philtrum_ratio",
        label="Филтрум-отношение",
        category=MetricCategory.PROPORTIONS,
        reference=0.33,
        tolerance=0.10,
        weight=0.7,
        unit="фильтрум/нижняя треть",
        source="[2] — фильтрум ≈ 1/3 высоты нижней трети лица",
    ),
    MetricSpec(
        key="gonial_angle",
        label="Gonial angle",
        category=MetricCategory.JAWLINE,
        reference=120.0,
        tolerance=12.0,
        weight=1.3,
        unit="°",
        source="[2],[10] — 120-130° у мужчин, ~125° у женщин в среднем",
        reference_by_gender={"male": 122.0, "female": 126.0},
    ),
    MetricSpec(
        key="bigonial_width",
        label="Bigonial width",
        category=MetricCategory.JAWLINE,
        reference=0.78,
        tolerance=0.08,
        weight=1.1,
        unit="доля ширины лица",
        source="[2] — ширина между углами челюсти ≈ 78% ширины лица",
    ),
    MetricSpec(
        key="jaw_angularity",
        label="Угловатость челюсти",
        category=MetricCategory.JAWLINE,
        reference=1.0,
        tolerance=0.25,
        weight=1.0,
        unit="коэф.",
        source="[10] — выраженность углов челюсти (половой диморфизм)",
        reference_by_gender={"male": 1.15, "female": 0.90},
    ),
    MetricSpec(
        key="facial_dimorphism",
        label="Половой диморфизм",
        category=MetricCategory.DIMORPHISM,
        reference=1.0,
        tolerance=0.22,
        weight=1.2,
        unit="коэф.",
        source="[8],[10] — диморфные черты ассоциируются с привлекательностью",
        reference_by_gender={"male": 1.10, "female": 0.92},
    ),
    MetricSpec(
        key="lower_upper_face_ratio",
        label="Отношение нижней/средней трети",
        category=MetricCategory.PROPORTIONS,
        reference=1.0,
        tolerance=0.18,
        weight=0.8,
        unit="коэф.",
        source="[4] — баланс средней и нижней третей лица",
    ),
    MetricSpec(
        key="harmony",
        label="Общая гармония",
        category=MetricCategory.HARMONY,
        reference=1.0,
        tolerance=0.15,
        weight=1.5,
        unit="композит",
        source="[5] — композитная согласованность пропорций",
        notes="Считается из согласованности остальных метрик, не из landmark напрямую.",
    ),
)

METRIC_BY_KEY: dict[str, MetricSpec] = {spec.key: spec for spec in METRIC_SPECS}

# Формула расчёта каждой метрики в терминах промежуточных переменных.
# Показывается в отчёте, чтобы результат можно было проверить вручную:
# подставив значения из блока «Переменные расчёта», получаем raw_value.
METRIC_FORMULAS: dict[str, str] = {
    "facial_symmetry": "mean(|d_left − d_right|) / face_width",
    "canthal_tilt": "(tilt(eye_left) + tilt(eye_right)) / 2",
    "facial_thirds": "min(t1, t2, t3) / max(t1, t2, t3)",
    "facial_fifths": "min(eye_w_l, intercanthal, eye_w_r) / max(…)",
    "golden_ratio": "face_height / cheek_width",
    "interpupillary_ratio": "ipd / face_width",
    "eye_spacing": "intercanthal / mean(eye_w_l, eye_w_r)",
    "brow_tilt": "(tilt(brow_left) + tilt(brow_right)) / 2",
    "nose_proportion": "nose_width / nose_height",
    "mouth_proportion": "mouth_width / lip_height",
    "philtrum_ratio": "philtrum / third_lower",
    "gonial_angle": "mean(∠(ramus, jaw, chin)_left, …_right)",
    "bigonial_width": "bigonial / face_width",
    "jaw_angularity": "bigonial / cheek_width",
    "facial_dimorphism": "(bigonial / cheek_width) · (ipd / face_width) / 0.46",
    "lower_upper_face_ratio": "third_lower / third_middle",
    "harmony": "10 · exp(−std(scores) / 1.8)",
}

# Человекочитаемые названия промежуточных переменных (для блока «Переменные»).
VARIABLE_LABELS: dict[str, str] = {
    "face_width": "Ширина лица",
    "face_height": "Высота лица",
    "aspect_ratio": "Отношение высоты к ширине",
    "third_upper": "Верхняя треть",
    "third_middle": "Средняя треть",
    "third_lower": "Нижняя треть",
    "lower_upper_face_ratio": "Нижняя треть / средняя треть",
    "ipd": "Межзрачковое расстояние",
    "nose_width_ratio": "Ширина носа / ширина лица",
    "mouth_width_ratio": "Ширина рта / ширина лица",
    "bigonial": "Межгониальный размер",
    "brow_to_jaw": "Бровь → угол челюсти",
}

# Веса категорий при агрегации в общий балл (сумма нормируется).
CATEGORY_WEIGHTS: dict[MetricCategory, float] = {
    MetricCategory.SYMMETRY: 0.20,
    MetricCategory.PROPORTIONS: 0.25,
    MetricCategory.JAWLINE: 0.20,
    MetricCategory.EYES: 0.15,
    MetricCategory.DIMORPHISM: 0.10,
    MetricCategory.HARMONY: 0.10,
}

MODEL_VERSION = "1.0.0"

# Порог качества фото: ниже — просим переснять (размытие/малый размер/наклон).
MIN_FACE_SIZE_PX = 120
MIN_IMAGE_SIDE_PX = 256
MAX_HEAD_TILT_DEG = 25.0
MIN_SHARPNESS = 40.0  # дисперсия лапласиана

DISCLAIMER = (
    "Оценка построена на математической модели пропорций лица и популяционных "
    "ориентирах. Это не медицинское заключение и не объективная истина о вашей "
    "внешности. Результаты зависят от качества фото, освещения и ракурса."
)

DEMO_DISCLAIMER = (
    "ДЕМО-РЕЖИМ: детектор лица не установлен, точки построены синтетически. "
    "Числа показывают работу конвейера и НЕ описывают вашу внешность."
)
