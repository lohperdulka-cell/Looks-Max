"""Экспорт отчёта в PDF (WeasyPrint) и HTML для печати.

WeasyPrint импортируется лениво: тяжёлая системная зависимость (pango/cairo)
нужна только при экспорте, приложение поднимается и без неё.
"""

from __future__ import annotations

from app.logging_config import get_logger
from app.schemas.reports import ReportOut

logger = get_logger(__name__)


def _score_color(score: float) -> str:
    if score >= 7.5:
        return "#16a34a"
    if score >= 5.0:
        return "#ca8a04"
    return "#dc2626"


def render_report_html(report: ReportOut, username: str = "") -> str:
    """Собирает печатную HTML-версию отчёта (общая для PDF и предпросмотра)."""
    rows = "".join(
        f"""
        <tr>
          <td>{m.label}</td>
          <td>{m.category_label}</td>
          <td>{"—" if m.raw_value is None else round(m.raw_value, 3)} {m.unit}</td>
          <td style="color:{_score_color(m.score)};font-weight:600">{m.score:.1f}</td>
        </tr>"""
        for m in sorted(report.metrics, key=lambda x: x.score, reverse=True)
    )

    categories = "".join(
        f"""
        <div class="cat">
          <span>{c.label}</span>
          <span class="cat-score" style="color:{_score_color(c.score)}">{c.score:.1f}</span>
        </div>"""
        for c in report.categories_detail
    )

    strengths = "".join(f"<li>{m.label} — {m.score:.1f}</li>" for m in report.strengths)
    growth = "".join(f"<li>{m.label} — {m.score:.1f}</li>" for m in report.growth)

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

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>FaceMetric — отчёт #{report.id}</title>
<style>
  @page {{ size: A4; margin: 18mm 14mm; }}
  body {{ font-family: "DejaVu Sans", sans-serif; color: #1f2430; font-size: 11px; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  h2 {{ font-size: 14px; margin: 18px 0 8px;
        border-bottom: 1px solid #e2e6ee; padding-bottom: 4px; }}
  h4 {{ font-size: 12px; margin: 0 0 4px; }}
  .muted {{ color: #6b7280; }}
  .hero {{ display: flex; gap: 24px; align-items: center; margin-bottom: 12px; }}
  .overall {{ font-size: 40px; font-weight: 700; }}
  .cats {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .cat {{ border: 1px solid #e2e6ee; border-radius: 8px; padding: 6px 10px; min-width: 120px; }}
  .cat-score {{ float: right; font-weight: 700; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 6px; }}
  th, td {{ text-align: left; padding: 5px 6px; border-bottom: 1px solid #eef1f6; }}
  th {{ background: #f6f8fb; font-size: 10px; text-transform: uppercase; letter-spacing: .04em; }}
  .cols {{ display: flex; gap: 20px; }}
  .cols > div {{ flex: 1; }}
  .rec {{ border: 1px solid #e2e6ee; border-radius: 8px; padding: 10px; margin-bottom: 8px; }}
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

  <h2>Все метрики</h2>
  <table>
    <thead><tr><th>Метрика</th><th>Категория</th><th>Значение</th><th>Балл</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>

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
