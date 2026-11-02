"""Тесты этапа 1: конфигурация, безопасность, ML-метрики, health-check."""

from __future__ import annotations

import numpy as np
import pytest
from app.config import Settings
from app.core.security import (
    create_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.ml.landmarks import FaceLandmarks
from app.ml.metrics import compute_raw_metrics
from app.ml.scoring import deviation_to_score, score_metrics, strengths_and_growth
from pydantic import ValidationError

# --- Конфигурация -------------------------------------------------------------


def test_settings_normalizes_postgres_dsn() -> None:
    s = Settings(database_url="postgresql://u:p@localhost/db")
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert not s.is_sqlite


def test_settings_sqlite_flag() -> None:
    s = Settings(database_url="sqlite+aiosqlite:///./x.db")
    assert s.is_sqlite


def test_allowed_origins_includes_base_url_and_extras() -> None:
    """CORS и CSRF-гвард берут один список origin из настроек.

    Если бы списки разошлись, разрешённый в CORS origin получал бы 403 от
    CSRF-гварда (или наоборот — появлялась бы дыра в защите).
    """
    s = Settings(
        base_url="https://app.example/",
        extra_allowed_origins=("https://cdn.example/", "https://cdn.example"),
    )
    assert s.allowed_origins == ("https://app.example", "https://cdn.example")


# --- Безопасность продакшена --------------------------------------------------


def test_production_rejects_default_secrets() -> None:
    """Прод не должен подниматься с секретами из репозитория.

    С публично известным JWT_SECRET любой может выпустить себе токен, поэтому
    запуск обязан падать, а не работать молча. Значения передаём явно: в
    conftest.py те же переменные заданы через окружение.
    """
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            app_env="production",
            secret_key="dev-insecure-secret-change-me",
            jwt_secret="dev-jwt-secret-change-me",
            cookie_secure=True,
        )
    assert "SECRET_KEY" in str(exc.value) and "JWT_SECRET" in str(exc.value)


def test_production_requires_secure_cookie() -> None:
    """Cookie без флага Secure уходят по HTTP и перехватываются."""
    with pytest.raises(ValidationError) as exc:
        Settings(
            _env_file=None,
            app_env="production",
            secret_key="s" * 32,
            jwt_secret="j" * 32,
            cookie_secure=False,
        )
    assert "COOKIE_SECURE" in str(exc.value)


def test_production_accepts_explicit_secrets() -> None:
    s = Settings(
        _env_file=None,
        app_env="production",
        secret_key="s" * 32,
        jwt_secret="j" * 32,
        cookie_secure=True,
    )
    assert s.is_production


def test_local_allows_dev_secrets() -> None:
    """Локальный запуск и тесты не должны требовать настройки секретов."""
    s = Settings(_env_file=None, app_env="local")
    assert not s.is_production


# --- Безопасность -------------------------------------------------------------


def test_password_hash_roundtrip() -> None:
    h = hash_password("S3cure-pass!")
    assert h.startswith("$argon2")
    assert verify_password("S3cure-pass!", h)
    assert not verify_password("wrong", h)


def test_password_hash_is_salted() -> None:
    assert hash_password("same") != hash_password("same")


def test_jwt_roundtrip_and_type_check() -> None:
    token = create_token(42, "access")
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == "42"
    assert payload["type"] == "access"

    import jwt

    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token, expected_type="refresh")


def test_hash_token_is_stable_and_irreversible() -> None:
    t = "some-opaque-token"
    assert hash_token(t) == hash_token(t)
    assert t not in hash_token(t)


# --- ML: метрики и скоринг ----------------------------------------------------


def _fake_landmarks(n: int = 468, seed: int = 7) -> FaceLandmarks:
    rng = np.random.default_rng(seed)
    return FaceLandmarks(points=rng.random((n, 2)), provider="mediapipe")


def test_deviation_to_score_monotonic() -> None:
    # Идеал — высокий балл, граница допуска — около 5, сильное отклонение — низко.
    assert deviation_to_score(0.0, 1.0) == pytest.approx(9.0, abs=1e-6)
    assert deviation_to_score(1.0, 1.0) == pytest.approx(5.0, abs=1e-6)
    assert deviation_to_score(5.0, 1.0) < 0.5
    assert 0.0 <= deviation_to_score(1e9, 1.0) <= 10.0


def test_compute_raw_metrics_produces_expected_keys() -> None:
    raw = compute_raw_metrics(_fake_landmarks())
    for key in ("facial_symmetry", "canthal_tilt", "facial_thirds", "gonial_angle"):
        assert key in raw.values
        assert np.isfinite(raw.values[key])


def test_score_metrics_aggregates_and_scores_in_range() -> None:
    raw = compute_raw_metrics(_fake_landmarks())
    result = score_metrics(raw, gender="male")
    assert 0.0 <= result.overall <= 10.0
    assert 0.0 <= result.harmony <= 10.0
    assert result.metrics
    assert all(0.0 <= m.score <= 10.0 for m in result.metrics)
    assert "harmony" in result.categories


def test_score_metrics_respects_weight_overrides() -> None:
    raw = compute_raw_metrics(_fake_landmarks())
    base = score_metrics(raw)
    overridden = score_metrics(raw, weight_overrides={"facial_symmetry": 100.0})
    # При экстремальном весе одной метрики общий балл смещается к её значению.
    sym = next(m for m in overridden.metrics if m.key == "facial_symmetry")
    assert abs(overridden.overall - sym.score) <= abs(base.overall - sym.score)


def test_strengths_and_growth_partition_metrics() -> None:
    raw = compute_raw_metrics(_fake_landmarks())
    result = score_metrics(raw)
    strengths, growth = strengths_and_growth(result, top_n=3)
    assert len(strengths) == 3
    assert strengths[0].score >= growth[0].score
    assert all(m.key != "harmony" for m in strengths)


# --- API ----------------------------------------------------------------------


async def test_health_endpoint(client) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] is True
    assert body["version"]


async def test_security_headers_present(client) -> None:
    resp = await client.get("/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in resp.headers


# --- Async SQLAlchemy: запрет ленивой загрузки ---------------------------------


def test_relationships_forbid_lazy_sql() -> None:
    """Все связи помечены lazy="raise_on_sql".

    Иначе обращение к незагруженной связи в async-сессии падает в проде
    ошибкой MissingGreenlet (ленивая загрузка выполняется вне greenlet).
    С raise_on_sql ошибка вылезает сразу и в тестах, а не у пользователя.
    """
    import app.models  # noqa: F401
    from app.db import Base
    from sqlalchemy import inspect as sa_inspect

    offenders: list[str] = []
    for mapper in Base.registry.mappers:
        for rel in sa_inspect(mapper.class_).relationships:
            if rel.lazy != "raise_on_sql":
                offenders.append(f"{mapper.class_.__name__}.{rel.key}={rel.lazy}")
    assert not offenders, f"Связи без raise_on_sql: {offenders}"


# --- Метрики: абсурдные значения отбрасываются --------------------------------


def test_absurd_metric_returns_none_and_is_logged() -> None:
    """Метрика вне физиологического диапазона не попадает в values.

    set() возвращает None, а само значение уходит в rejected — иначе
    «63.77» вместо пропорции губ попал бы в отчёт как реальный результат.
    """
    from app.ml.metrics import RawMetrics

    raw = RawMetrics()
    assert raw.set("mouth_proportion", 63.77) is None
    assert "mouth_proportion" not in raw.values
    assert raw.rejected["mouth_proportion"] == pytest.approx(63.77)


def test_plausible_metric_is_kept() -> None:
    """Нормальное значение сохраняется и возвращается из set()."""
    from app.ml.metrics import RawMetrics

    raw = RawMetrics()
    assert raw.set("mouth_proportion", 1.6) == pytest.approx(1.6)
    assert raw.values["mouth_proportion"] == pytest.approx(1.6)
    assert "mouth_proportion" not in raw.rejected


def test_nan_metric_is_rejected() -> None:
    """NaN не проходит проверку конечности и попадает в rejected."""
    from app.ml.metrics import RawMetrics

    raw = RawMetrics()
    assert raw.set("golden_ratio", float("nan")) is None
    assert "golden_ratio" not in raw.values


def test_rejected_metrics_reach_score_result() -> None:
    """Отброшенные метрики доезжают до ScoreResult и попадают в missing."""
    from app.ml.metrics import RawMetrics
    from app.ml.scoring import score_metrics

    raw = RawMetrics()
    raw.set("mouth_proportion", 63.77)
    result = score_metrics(raw)
    assert result.rejected.get("mouth_proportion") == pytest.approx(63.77)
    assert "mouth_proportion" in result.missing


def test_metrics_are_scale_invariant() -> None:
    """Пропорции не зависят от разрешения снимка.

    Если бы метрики считались в пикселях, увеличение кадра (например, с 800x600
    до 1600x1200) меняло бы результат. Берём ОДНО лицо (одни нормализованные
    точки) и прогоняем его в двух размерах кадра: все коэффициенты и углы
    обязаны совпасть.
    """
    from app.ml.demo_landmarks import synthetic_bbox, synthetic_points

    img = np.zeros((600, 800, 3), dtype=np.uint8)
    pts = synthetic_points(img)
    bbox = synthetic_bbox(pts)

    def raw_for(w: int, h: int):
        lm = FaceLandmarks(points=pts, provider="demo", image_size=(w, h), bbox=bbox)
        return compute_raw_metrics(lm, gender="male")

    small = raw_for(800, 600)
    large = raw_for(1600, 1200)

    assert small.values, "метрики не посчитались"
    assert set(small.values) == set(large.values)
    for key, value in small.values.items():
        assert value == pytest.approx(large.values[key], abs=1e-9), key


def test_metrics_are_dimensionless_magnitudes() -> None:
    """Метрики не выглядят «пиксельными».

    Коэффициенты-отношения и доли держатся в пределах десятков, углы — в
    0..180 градусов. Значение порядка сотен и тысяч означало бы, что где-то
    остались пиксели вместо коэффициента (тот самый «63.77»).
    """
    from app.ml.demo_landmarks import synthetic_bbox, synthetic_points

    img = np.zeros((600, 480, 3), dtype=np.uint8)
    pts = synthetic_points(img)
    lm = FaceLandmarks(points=pts, provider="demo", image_size=(480, 600), bbox=synthetic_bbox(pts))
    raw = compute_raw_metrics(lm, gender="male")

    angles = {"canthal_tilt", "brow_tilt", "gonial_angle"}
    for key, value in raw.values.items():
        if key in angles:
            assert 0.0 <= value <= 180.0, f"{key}={value} вне диапазона углов"
        else:
            assert value < 30.0, f"{key}={value} похоже на пиксельную величину"
