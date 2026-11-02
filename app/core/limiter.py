"""Общий rate-limiter (slowapi) — вынесен отдельно, чтобы роутеры могли его импортировать."""

from __future__ import annotations

from slowapi import Limiter

from app.config import settings
from app.core.client_ip import client_ip

limiter = Limiter(
    key_func=client_ip,
    default_limits=[settings.rate_limit_default] if settings.rate_limit_enabled else [],
    enabled=settings.rate_limit_enabled,
)
