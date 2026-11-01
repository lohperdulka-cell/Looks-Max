"""Экспорт отчёта в PDF (WeasyPrint) и HTML для печати.

WeasyPrint импортируется лениво: тяжёлая системная зависимость (pango/cairo)
нужна только при экспорте, приложение поднимается и без неё.
"""

from __future__ import annotations

from app.logging_config import get_logger
from app.schemas.reports import MetricOut, ReportOut

logger = get_logger(__name__)

# Градиент «плохо → хорошо» для шкал метрик. Совпадает с веб-версией.
GAUGE_GRADIENT = (
    "linear-gradient(90deg, #dc2626 0%, #ea580c 28%, #eab308 50%, #65a30d 74%, #16a34a 100%)"
)


def _score_color(score: float) -> str:
    if score >= 7.5:
        return "#16a34a"
    if score >= 5.0:
        return "#ca8a04"
    return "#dc2626"


def _fmt(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _gauge(score: float) -> str:
    """Полоса-градиент с маркером на позиции балла (0..10 → 0..100 %)."""
    pct = max(0.0, min(100.0, score * 10))
    return f'<div class="gauge"><span class="gauge-marker" style="left:{pct:.1f}%"></span></div>'


def _metric_block(m: MetricOut) -> str:
    """Карточка метрики: шкала, значение, эталон и пояснение."""
    unit = f" {m.unit}" if m.unit else ""
    deviation = ""
    if m.deviation is not None and m.reference_value is not None:
        sign = "+" if m.deviation > 0 else ""
        deviation = f" · отклонение {sign}{m.deviation:.2f}"
    notes = f'<p class="m-note">{m.notes}</p>' if m.notes else ""
    source = f'<p class="m-src">Источник: {m.source}</p>' if m.source else ""
    formula = f'<p class="m-formula"><code>{m.formula}</code></p>' if m.formula else ""
    return f"""
      <div class="metric">
        <div class="m-head">
          <span class="m-label">{m.label}</span>
          <span class="m-cat">{m.category_label}</span>
          <span class="m-score" style="color:{_score_color(m.score)}">{m.score:.1f}</span>
        </div>
        {_gauge(m.score)}
        <p class="m-values">Ваше значение: {_fmt(m.raw_value)}{unit}
           · эталон: {_fmt(m.reference_value)}{deviation}</p>
        {formula}
        {notes}
        {source}
      </div>"""


def _category_block(report: ReportOut) -> str:
    """Категории с полосами-градиентами."""
    return "".join(
        f"""
        <div class="cat">
          <div class="cat-head"><span>{c.label}</span>
            <span style="color:{_score_color(c.score)}">{c.score:.1f}</span></div>
          {_gauge(c.score)}
          <p class="cat-sub">{c.metrics_count} метрик · вес {c.weight:.2f}</p>
        </div>"""
        for c in report.categories_detail
    )


def _computation_block(report: ReportOut) -> str:
    """Печатный блок «Как посчитан итог»: вклад категорий и переменные расчёта."""
    comp = report.computation
    if not comp.variables and not comp.category_contributions:
        return ""

    rows = "".join(
        f"""<tr>
              <td>{c.label}</td>
              <td style="color:{_score_color(c.score)};font-weight:600">{c.score:.2f}</td>
              <td>{c.weight:.2f}</td>
              <td>{c.contribution:.3f}</td>
              <td class="muted">{c.share * 100:.1f}%</td>
            </tr>"""
        for c in comp.category_contributions
    )
    variables = "".join(
        f"""<tr><td>{v.label}</td><td class="muted"><code>{v.key}</code></td>
                <td>{v.value:.4f}</td></tr>"""
        for v in comp.variables
    )
    formula = (
        f'<p class="muted">{comp.overall_formula} → {comp.overall_numerator:.3f} / '
        f"{comp.overall_denominator:.2f}</p>"
        if comp.overall_formula
        else ""
    )
    return f"""
  <h2>Как посчитан итог</h2>
  {formula}
  <table class="tbl">
    <thead>
      <tr><th>Категория</th><th>Балл</th><th>Вес</th><th>Вклад</th><th>Доля</th></tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <h2>Переменные расчёта</h2>
  <p class="muted">Промежуточные величины, из которых собраны метрики.
     Все длины нормированы на ширину или высоту лица.</p>
  <table class="tbl">
    <thead><tr><th>Переменная</th><th>Обозначение</th><th>Значение</th></tr></thead>
    <tbody>{variables}</tbody>
  </table>"""


def render_report_html(report: ReportOut, username: str = "") -> str:
    """Собирает печатную HTML-версию отчёта (общая для PDF и предпросмотра)."""
    metrics_sorted = sorted(report.metrics, key=lambda x: x.score, reverse=True)
    metrics_html = "".join(_metric_block(m) for m in metrics_sorted)

    categories = _category_block(report)

    strengths = "".join(
        f'<li>{m.label} — <strong style="color:{_score_color(m.score)}">{m.score:.1f}</strong></li>'
        for m in report.strengths
    )
    growth = "".join(
        f'<li>{m.label} — <strong style="color:{_score_color(m.score)}">{m.score:.1f}</strong></li>'
        for m in report.growth
    )

    recommendations = "".join(
        f"""
        <div class="rec">
          <h4>{r.priority}. {r.label}</h4>
          <p>{r.text}</p>
          <ul>{"".join(f"<li>{a}</li>" for a in r.actions)}</ul>
          <p class="src">Источник: {r.source}</p>
        </div>"""
        for r in report.recommendations
    )

    computation = _computation_block(report)

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>FaceMetric — отчёт #{report.id}</title>
<style>
  @page {{ size: A4; margin: 16mm 13mm; }}
  body {{ font-family: "DejaVu Sans", sans-serif; color: #1f2430; font-size: 10.5px; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  h2 {{ font-size: 13.5px; margin: 18px 0 8px;
        border-bottom: 1px solid #e2e6ee; padding-bottom: 4px; }}
  h4 {{ font-size: 11.5px; margin: 0 0 4px; }}
  .muted {{ color: #6b7280; }}
  .hero {{ display: flex; gap: 24px; align-items: center; margin-bottom: 12px; }}
  .overall {{ font-size: 40px; font-weight: 700; }}

  /* Шкала-градиент: полоса «плохо → хорошо» с маркером значения. */
  .gauge {{ position: relative; height: 9px; border-radius: 999px;
            background: {GAUGE_GRADIENT}; margin: 5px 0 3px; }}
  .gauge-marker {{ position: absolute; top: -3px; width: 3px; height: 15px;
                   margin-left: -1.5px; border-radius: 2px; background: #1f2430; }}

  .cats {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .cat {{ border: 1px solid #e2e6ee; border-radius: 8px; padding: 7px 10px;
          width: 31%; box-sizing: border-box; }}
  .cat-head {{ display: flex; justify-content: space-between; font-weight: 600; }}
  .cat-sub {{ margin: 2px 0 0; color: #6b7280; font-size: 9px; }}

  .metrics {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .metric {{ border: 1px solid #e2e6ee; border-radius: 8px; padding: 8px 10px;
             width: 48%; box-sizing: border-box; }}
  .m-head {{ display: flex; align-items: baseline; gap: 6px; }}
  .m-label {{ font-weight: 600; }}
  .m-cat {{ color: #6b7280; font-size: 8.5px; text-transform: uppercase;
            letter-spacing: .04em; margin-left: auto; }}
  .m-score {{ font-weight: 700; }}
  .m-values {{ margin: 0; font-size: 9px; color: #4b5563; }}
  .m-note {{ margin: 3px 0 0; font-size: 8.5px; color: #6b7280; }}
  .m-src {{ margin: 2px 0 0; font-size: 8px; color: #9aa3b2; }}
  .m-formula {{ margin: 2px 0 0; font-size: 8.5px; }}
  .m-formula code {{ font-family: "DejaVu Sans Mono", monospace; color: #1d4ed8; }}

  .tbl {{ width: 100%; border-collapse: collapse; font-size: 9.5px;
          break-inside: avoid; }}
  .tbl th {{ text-align: left; border-bottom: 1px solid #cbd5e1; padding: 4px 6px;
             color: #475569; font-weight: 600; }}
  .tbl td {{ border-bottom: 1px solid #eef1f6; padding: 4px 6px; }}
  .tbl code {{ font-family: "DejaVu Sans Mono", monospace; font-size: 8.5px;
               color: #1d4ed8; }}

  .cols {{ display: flex; gap: 20px; }}
  .cols > div {{ flex: 1; }}
  .rec {{ border: 1px solid #e2e6ee; border-radius: 8px; padding: 10px; margin-bottom: 8px;
          break-inside: avoid; }}
  .rec ul {{ margin: 4px 0 0 16px; padding: 0; }}
  .src {{ color: #6b7280; font-size: 9px; margin: 6px 0 0; }}
  .disclaimer {{ margin-top: 16px; padding: 10px; background: #f6f8fb; border-radius: 8px;
                 color: #4b5563; font-size: 9.5px; }}
</style>
</head>
<body>
  <div class="hero">
    <div>
      <h1>FaceMetric</h1>
      <p class="muted">Отчёт #{report.id} · {report.created_at:%d.%m.%Y}
         · модель {report.model_version}</p>
      {f'<p class="muted">Пользователь: {username}</p>' if username else ""}
    </div>
    <div style="margin-left:auto;text-align:right">
      <div class="overall" style="color:{_score_color(report.overall_score)}">
        {report.overall_score:.1f}</div>
      <div class="muted">общий балл · гармония {report.harmony_score or 0:.1f}</div>
    </div>
  </div>

  <h2>Категории</h2>
  <div class="cats">{categories}</div>

  <div class="cols">
    <div><h2>Сильные стороны</h2><ul>{strengths}</ul></div>
    <div><h2>Зоны роста</h2><ul>{growth}</ul></div>
  </div>

  <h2>Метрики: шкалы и пояснения</h2>
  <div class="metrics">{metrics_html}</div>

  {computation}

  <h2>Рекомендации</h2>
  {recommendations or '<p class="muted">Явных зон роста не выявлено.</p>'}

  <div class="disclaimer">{report.disclaimer}</div>
</body>
</html>"""


def render_pdf(report: ReportOut, username: str = "") -> bytes:
    """Рендерит отчёт в PDF. Бросает RuntimeError, если WeasyPrint недоступен."""
    try:
        from weasyprint import HTML
    except Exception as exc:
        logger.warning("reports.pdf.unavailable", error=str(exc))
        raise RuntimeError("Экспорт в PDF недоступен: не установлен WeasyPrint") from exc

    html = render_report_html(report, username=username)
    return HTML(string=html).write_pdf()
