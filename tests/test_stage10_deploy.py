"""Этап 10: деплой (Render/Docker) — демо-провайдер, флаги, health.

Проверяем то, что ломается именно при деплое без тяжёлых ML-зависимостей:
провайдер `demo` не подтягивает InsightFace/MediaPipe, флаг is_demo доходит
до клиента, а /health не пытается грузить веса в демо-режиме.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from app.ml import landmarks as lm_module
from app.ml.demo_landmarks import synthetic_bbox, synthetic_points
from app.ml.landmarks import LandmarkError, extract_landmarks
from PIL import Image


def _jpeg_bytes(size: tuple[int, int] = (640, 640), seed: int = 7) -> bytes:
    rng = np.random.default_rng(seed)
    arr = rng.integers(60, 200, size=(*size, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, format="JPEG")
    return buf.getvalue()


def _bgr(size: tuple[int, int] = (640, 640), seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(60, 200, size=(*size, 3), dtype=np.uint8)


# --- Демо-провайдер ----------------------------------------------------------


def test_demo_points_shape_and_range():
    """106 точек (схема InsightFace): в 68-точечной нет точки лба."""
    points = synthetic_points(_bgr())
    assert points.shape == (106, 2)
    assert points.min() >= 0.0
    assert points.max() <= 1.0


def test_demo_points_deterministic_for_same_image():
    image = _bgr(seed=11)
    assert np.allclose(synthetic_points(image), synthetic_points(image))


def test_demo_points_differ_across_images():
    a = synthetic_points(_bgr(seed=1))
    b = synthetic_points(_bgr(seed=2))
    assert not np.allclose(a, b)


def test_demo_bbox_matches_points():
    points = synthetic_points(_bgr())
    x1, y1, x2, y2 = synthetic_bbox(points)
    assert x1 == pytest.approx(float(points[:, 0].min()))
    assert y2 == pytest.approx(float(points[:, 1].max()))
    assert x2 > x1 and y2 > y1


def test_demo_points_survive_non_square_frame():
    points = synthetic_points(_bgr(size=(800, 400)))
    assert points.shape == (106, 2)
    assert points.min() >= 0.0 and points.max() <= 1.0


def test_extract_landmarks_uses_demo_when_configured(monkeypatch):
    monkeypatch.setattr(lm_module.settings, "ml_enabled", True)
    monkeypatch.setattr(lm_module.settings, "ml_provider_primary", "demo")
    monkeypatch.setattr(lm_module.settings, "ml_provider_fallback", "demo")
    monkeypatch.setattr(lm_module.settings, "ml_provider_demo", True)

    result = extract_landmarks(_bgr())
    assert result.provider == "demo"
    assert len(result.points) == 106


def test_extract_landmarks_demo_not_used_unless_enabled(monkeypatch):
    """Без ML_PROVIDER_DEMO демо-провайдер не подставляется молча."""
    monkeypatch.setattr(lm_module.settings, "ml_enabled", True)
    monkeypatch.setattr(lm_module.settings, "ml_provider_primary", "insightface")
    monkeypatch.setattr(lm_module.settings, "ml_provider_fallback", "mediapipe")
    monkeypatch.setattr(lm_module.settings, "ml_provider_demo", False)
    monkeypatch.setattr(lm_module, "_load_insightface", lambda: None)
    monkeypatch.setattr(lm_module, "_load_mediapipe", lambda: None)

    with pytest.raises(LandmarkError):
        extract_landmarks(_bgr())


def test_extract_landmarks_falls_back_to_demo(monkeypatch):
    """Реальные провайдеры недоступны → demo подхватывает конвейер."""
    monkeypatch.setattr(lm_module.settings, "ml_enabled", True)
    monkeypatch.setattr(lm_module.settings, "ml_provider_primary", "insightface")
    monkeypatch.setattr(lm_module.settings, "ml_provider_fallback", "mediapipe")
    monkeypatch.setattr(lm_module.settings, "ml_provider_demo", True)

    def _boom(_img):
        raise RuntimeError("нет весов модели")

    monkeypatch.setitem(lm_module._EXTRACTORS, "insightface", _boom)
    monkeypatch.setitem(lm_module._EXTRACTORS, "mediapipe", _boom)

    result = extract_landmarks(_bgr())
    assert result.provider == "demo"
    assert result.extra.get("demo") is True


def test_extract_landmarks_respects_ml_disabled(monkeypatch):
    monkeypatch.setattr(lm_module.settings, "ml_enabled", False)
    with pytest.raises(LandmarkError, match="ML отключён"):
        extract_landmarks(_bgr())


# --- Дисклеймеры -------------------------------------------------------------


def test_demo_disclaimer_marks_synthetic_points():
    from app.ml.constants import DEMO_DISCLAIMER, DISCLAIMER

    assert "ДЕМО" in DEMO_DISCLAIMER
    assert DEMO_DISCLAIMER != DISCLAIMER


def test_is_demo_report_reads_summary_flag():
    """Флаг демо-режима берётся из summary отчёта (реальная модель Report)."""
    from app.models.report import Report
    from app.services.reports import is_demo_report

    assert is_demo_report(Report(summary={"is_demo": True})) is True
    assert is_demo_report(Report(summary={"missing": []})) is False
    assert is_demo_report(Report(summary=None)) is False


# --- Health ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_reports_demo_mode_without_loading_weights(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ml_enabled", True)
    monkeypatch.setattr(settings, "ml_provider_primary", "demo")
    monkeypatch.setattr(settings, "ml_provider_fallback", "demo")
    monkeypatch.setattr(settings, "ml_provider_demo", True)

    called = {"insightface": False}

    def _spy():
        called["insightface"] = True
        return None

    monkeypatch.setattr(lm_module, "_load_insightface", _spy)

    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ml"]["detectors"] == "demo"
    assert called["insightface"] is False
    assert body["ml"]["primary"] == "demo"


@pytest.mark.asyncio
async def test_health_is_ok_without_ml(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ml_enabled", False)
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["database"] is True


# --- Страницы: демо-плашка и онбординг ---------------------------------------


@pytest.mark.asyncio
async def test_onboarding_flag_present_for_guest(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert 'data-onboarding="0"' in resp.text


@pytest.mark.asyncio
async def test_onboarding_flag_present_for_user(client):
    resp = await client.post(
        "/api/auth/register",
        json={"username": "onboarduser", "password": "Passw0rd1", "biometric_consent": True},
    )
    assert resp.status_code == 201

    resp = await client.get("/")
    assert resp.status_code == 200
    assert 'data-onboarding="1"' in resp.text


def test_fm_escape_exported_in_base_js():
    from pathlib import Path

    js = Path("app/static/js/app.js").read_text(encoding="utf-8")
    assert "window.fmEscape = escapeHtml" in js
    assert "escapeHtml" in js.split("window.fm = ")[1]


# --- Исправления метрик, найденные при подготовке деплоя --------------------


def test_canthal_tilt_symmetric_for_both_eyes():
    """Левая и правая стороны дают одинаковый наклон (раньше выходило ~90°)."""
    import numpy as np
    from app.ml.metrics import _legacy_index_map, _tilt_deg

    m = _legacy_index_map()
    pts = np.zeros((68, 2))
    pts[m["left_eye_inner"]] = (0.43, 0.40)
    pts[m["left_eye_outer"]] = (0.33, 0.395)
    pts[m["right_eye_inner"]] = (0.57, 0.40)
    pts[m["right_eye_outer"]] = (0.67, 0.395)

    left = _tilt_deg(pts[m["left_eye_inner"]], pts[m["left_eye_outer"]])
    right = _tilt_deg(pts[m["right_eye_inner"]], pts[m["right_eye_outer"]])

    assert left == pytest.approx(right, abs=1e-6)
    assert 0 < left < 10  # наружный уголок выше → положительный небольшой угол
    assert (left + right) / 2 < 45


def test_canthal_tilt_negative_when_outer_corner_lower():
    import numpy as np
    from app.ml.metrics import _tilt_deg

    inner = np.asarray([0.57, 0.40])
    outer = np.asarray([0.67, 0.41])  # наружный ниже
    assert _tilt_deg(inner, outer) < 0


def test_symmetry_axis_ignores_nose_offset():
    """Смещение кончика носа по x не должно создавать «асимметрию»."""
    import numpy as np
    from app.ml.demo_landmarks import synthetic_points
    from app.ml.landmarks import FaceLandmarks
    from app.ml.metrics import compute_raw_metrics

    image = np.random.default_rng(5).integers(60, 200, (700, 700, 3), dtype=np.uint8)
    pts = synthetic_points(image)
    base = compute_raw_metrics(FaceLandmarks(points=pts, provider="demo")).values

    shifted = pts.copy()
    shifted[30, 0] += 0.05  # кончик носа (индекс 30 в 68-точечной схеме)
    after = compute_raw_metrics(FaceLandmarks(points=shifted, provider="demo")).values

    assert base["facial_symmetry"] == pytest.approx(after["facial_symmetry"], abs=1e-9)


def test_demo_grid_is_mirror_symmetric():
    """Сетка симметрична по построению: метрики симметрии не должны шуметь."""
    import numpy as np
    from app.ml.demo_landmarks import _BASE

    for row in _BASE:
        mirrored = np.asarray([1.0 - row[0], row[1]])
        assert np.any(np.all(np.isclose(_BASE, mirrored, atol=1e-9), axis=1))


def test_demo_report_metrics_close_to_reference():
    """Откалиброванный шаблон даёт правдоподобный демо-отчёт."""
    import numpy as np
    from app.ml.demo_landmarks import synthetic_points
    from app.ml.landmarks import FaceLandmarks
    from app.ml.metrics import compute_raw_metrics
    from app.ml.scoring import score_metrics

    image = np.random.default_rng(1).integers(60, 200, (700, 700, 3), dtype=np.uint8)
    raw = compute_raw_metrics(FaceLandmarks(points=synthetic_points(image), provider="demo"))
    result = score_metrics(raw)

    assert 3.0 <= result.overall <= 8.0
    assert result.categories["symmetry"] >= 7.0
    assert raw.values["facial_thirds"] > 0.5  # без точки лба было ровно 0.0
