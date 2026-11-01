"""Перевод сырых метрик в оценки 0..10, агрегация и гармония.

Математическая модель оценки:
  * для каждой метрики есть эталон (reference) и допуск (tolerance);
  * отклонение нормируется: z = |raw - reference| / tolerance;
  * перевод в балл — плавная (логистическая) функция:
        score = 10 / (1 + exp(k * (z - 1)))
    где k = ln(9) подобран так, что при z=0 (идеал) score=9.0, при z=1 (на
    границе допуска) score=5.0, при z=2 score≈1.0. Это даёт «мягкий» штраф
    вблизи эталона и резкий при сильных отклонениях, без обрывов.
  * метрики-«отклонения» (например, facial_symmetry) задают эталон = 0, поэтому
    штрафуется любой уход в обе стороны симметрично.

Агрегация:
  * внутри категории — взвешенное среднее по весам метрик;
  * общий балл — взвешенное среднее по категориям (CATEGORY_WEIGHTS);
  * harmony — композит: 1 - нормированная дисперсия оценок (согласованность).

Веса могут быть переопределены из админки (таблица metric_weights) — тогда
они приходят в overrides.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.ml.constants import (
    CATEGORY_LABELS,
    CATEGORY_WEIGHTS,
    METRIC_BY_KEY,
    METRIC_FORMULAS,
    METRIC_SPECS,
    MetricCategory,
    MetricSpec,
)
from app.ml.metrics import RawMetrics

# Крутизна логистики: при z=1 (граница допуска) даёт ровно 5.0.
LOGISTIC_K = math.log(9.0)  # 10/(1+exp(k*0)) = 5 при z=1 => exp(k)=9

# Масштаб спада гармонии: при std≈1.8 (типичный разброс метрик) балл ≈ 3.7.
HARMONY_DECAY = 1.8


@dataclass(slots=True)
class MetricScore:
    """Оценка одной метрики."""

    key: str
    label: str
    category: str
    raw_value: float | None
    reference_value: float
    deviation: float
    score: float
    weight: float
    unit: str = ""
    source: str = ""
    notes: str = ""
    # Прозрачность расчёта: допуск, формула и нормированное отклонение.
    tolerance: float = 0.0
    formula: str = ""
    z_score: float = 0.0


@dataclass(slots=True)
class ScoreResult:
    """Итог скоринга: список метрик, категории, общий балл, гармония."""

    metrics: list[MetricScore] = field(default_factory=list)
    categories: dict[str, float] = field(default_factory=dict)
    overall: float = 0.0
    harmony: float = 0.0
    missing: list[str] = field(default_factory=list)
    model_version: str = "1.0.0"
    # Промежуточные величины геометрии (ширина лица, трети, ipd и т.д.).
    auxiliary: dict[str, float] = field(default_factory=dict)
    # Взвешенный вклад каждой категории в общий балл.
    category_contributions: dict[str, float] = field(default_factory=dict)


def _resolve_reference(spec: MetricSpec, gender: str | None, override: float | None) -> float:
    """Выбирает эталон: override из админки > половой > общий."""
    if override is not None:
        return override
    if gender and gender in spec.reference_by_gender:
        return spec.reference_by_gender[gender]
    return spec.reference


def deviation_to_score(deviation: float, tolerance: float) -> float:
    """Переводит абсолютное отклонение в балл 0..10 (логистическая шкала).

    deviation — модуль отклонения от эталона в единицах метрики.
    tolerance — отклонение, соответствующее баллу 5.0.
    """
    if tolerance <= 0:
        return 10.0
    z = abs(deviation) / tolerance
    # Численно устойчивая логистика: 10/(1+exp(k*(z-1))).
    # При больших аргументах exp переполняется, поэтому ветвим по знаку.
    arg = LOGISTIC_K * (z - 1.0)
    if arg >= 0:
        exp_neg = math.exp(-arg) if arg < 700 else 0.0
        score = 10.0 * exp_neg / (1.0 + exp_neg)
    else:
        exp_pos = math.exp(arg) if arg > -700 else 0.0
        score = 10.0 / (1.0 + exp_pos)
    return float(max(0.0, min(10.0, score)))


def score_metrics(
    raw: RawMetrics,
    gender: str | None = None,
    overrides: dict[str, float] | None = None,
    weight_overrides: dict[str, float] | None = None,
    enabled: dict[str, bool] | None = None,
) -> ScoreResult:
    """Считает баллы по всем метрикам и агрегирует их.

    overrides — переопределения эталонов (ключ -> значение);
    weight_overrides — переопределения весов;
    enabled — отключение метрик из админки.
    """
    overrides = overrides or {}
    weight_overrides = weight_overrides or {}
    enabled = enabled or {}

    result = ScoreResult(auxiliary=dict(raw.auxiliary))
    scored: list[MetricScore] = []

    for spec in METRIC_SPECS:
        if enabled.get(spec.key, True) is False:
            continue
        if spec.key == "harmony":
            continue  # гармония считается отдельно, из остальных

        raw_value = raw.values.get(spec.key)
        if raw_value is None:
            result.missing.append(spec.key)
            continue

        reference = _resolve_reference(spec, gender, overrides.get(spec.key))
        deviation = raw_value - reference
        score = deviation_to_score(deviation, spec.tolerance)
        weight = weight_overrides.get(spec.key, spec.weight)

        scored.append(
            MetricScore(
                key=spec.key,
                label=spec.label,
                category=spec.category.value,
                raw_value=raw_value,
                reference_value=reference,
                deviation=deviation,
                score=score,
                weight=weight,
                unit=spec.unit,
                source=spec.source,
                notes=spec.notes,
                tolerance=spec.tolerance,
                formula=METRIC_FORMULAS.get(spec.key, ""),
                z_score=abs(deviation) / spec.tolerance if spec.tolerance > 0 else 0.0,
            )
        )

    # --- Агрегация по категориям (взвешенное среднее).
    by_category: dict[str, list[MetricScore]] = {}
    for m in scored:
        by_category.setdefault(m.category, []).append(m)

    for cat, items in by_category.items():
        total_w = sum(max(i.weight, 0.0) for i in items) or 1.0
        cat_score = sum(i.score * max(i.weight, 0.0) for i in items) / total_w
        result.categories[cat] = round(cat_score, 2)

    # --- Общий балл: взвешенное среднее по категориям.
    used_weight = 0.0
    overall_acc = 0.0
    for cat_value, cat_score in result.categories.items():
        try:
            cat_enum = MetricCategory(cat_value)
        except ValueError:
            continue
        w = CATEGORY_WEIGHTS.get(cat_enum, 0.0)
        overall_acc += cat_score * w
        used_weight += w
        # Вклад категории в общий балл: видно, какая тянет итог вверх или вниз.
        result.category_contributions[cat_value] = round(cat_score * w, 4)
    result.overall = round(overall_acc / used_weight, 2) if used_weight > 0 else 0.0

    # --- Гармония: согласованность оценок. Чем меньше разброс, тем выше.
    scores = [m.score for m in scored]
    if len(scores) >= 2:
        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        std = math.sqrt(variance)
        # Плавный спад: std=0 -> 10, std=HARMONY_DECAY -> ~3.7, без жёсткого нуля.
        harmony_score = max(0.0, min(10.0, 10.0 * math.exp(-std / HARMONY_DECAY)))
    else:
        harmony_score = result.overall

    result.harmony = round(harmony_score, 2)
    harmony_spec = METRIC_BY_KEY["harmony"]
    scored.append(
        MetricScore(
            key="harmony",
            label="Общая гармония",
            category=MetricCategory.HARMONY.value,
            raw_value=None,
            reference_value=1.0,
            deviation=0.0,
            score=harmony_score,
            weight=weight_overrides.get("harmony", 1.5),
            unit="композит",
            source="[5] — композитная согласованность пропорций",
            notes=harmony_spec.notes,
            tolerance=harmony_spec.tolerance,
            formula=METRIC_FORMULAS["harmony"],
        )
    )
    result.categories[MetricCategory.HARMONY.value] = round(harmony_score, 2)

    # Пересчитываем общий балл с учётом гармонии как отдельной категории.
    used_weight = 0.0
    overall_acc = 0.0
    result.category_contributions = {}
    for cat_value, cat_score in result.categories.items():
        try:
            cat_enum = MetricCategory(cat_value)
        except ValueError:
            continue
        w = CATEGORY_WEIGHTS.get(cat_enum, 0.0)
        overall_acc += cat_score * w
        used_weight += w
        result.category_contributions[cat_value] = round(cat_score * w, 4)
    result.overall = round(overall_acc / used_weight, 2) if used_weight > 0 else 0.0

    result.metrics = scored
    return result


def category_label(category: str) -> str:
    """Человекочитаемое название категории."""
    try:
        return CATEGORY_LABELS[MetricCategory(category)]
    except (ValueError, KeyError):
        return category


def strengths_and_growth(
    result: ScoreResult, top_n: int = 4
) -> tuple[list[MetricScore], list[MetricScore]]:
    """Возвращает (сильные стороны, зоны роста) по оценкам метрик."""
    scored = [m for m in result.metrics if m.key != "harmony"]
    ordered = sorted(scored, key=lambda m: m.score, reverse=True)
    strengths = ordered[:top_n]
    growth = list(reversed(ordered[-top_n:])) if len(ordered) > top_n else list(reversed(ordered))
    return strengths, growth
