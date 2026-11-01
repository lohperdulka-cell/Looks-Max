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

# --- Конфигурация -------------------------------------------------------------


def test_settings_normalizes_postgres_dsn() -> None:
    s = Settings(database_url="postgresql://u:p@localhost/db")
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert not s.is_sqlite


def test_settings_sqlite_flag() -> None:
    s = Settings(database_url="sqlite+aiosqlite:///./x.db")
    assert s.is_sqlite


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
