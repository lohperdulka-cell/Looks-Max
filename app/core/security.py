"""Безопасность: хэширование паролей (argon2), JWT access/refresh, CSRF-токены."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.config import settings

# Argon2id — рекомендованный вариант (RFC 9106). Параметры сбалансированы
# для веб-нагрузки: ~50-100 мс на хэш на типичном CPU.
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)

TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    """Возвращает argon2id-хэш пароля."""
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Проверяет пароль. Возвращает False вместо исключения при несовпадении."""
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def needs_rehash(password_hash: str) -> bool:
    """True, если хэш устарел и его стоит пересчитать при следующем входе."""
    try:
        return _ph.check_needs_rehash(password_hash)
    except Exception:
        return True


def create_token(
    subject: str | int,
    token_type: TokenType,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Создаёт подписанный JWT (HS256)."""
    now = datetime.now(UTC)
    if expires_delta is None:
        expires_delta = (
            timedelta(minutes=settings.access_token_ttl_minutes)
            if token_type == "access"
            else timedelta(days=settings.refresh_token_ttl_days)
        )
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: TokenType | None = None) -> dict[str, Any]:
    """Декодирует и валидирует JWT. Бросает jwt.PyJWTError при ошибке."""
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if expected_type and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("Неверный тип токена")
    return payload


def hash_token(token: str) -> str:
    """Стабильный хэш токена для хранения в БД (без секрета в открытом виде)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_opaque_token(nbytes: int = 32) -> str:
    """Криптостойкий случайный токен (для refresh, сброса пароля, публичных ссылок)."""
    return secrets.token_urlsafe(nbytes)


def sign_csrf_token(session_id: str) -> str:
    """CSRF-токен = HMAC(secret, session_id). Привязывает форму к cookie-сессии."""
    return hmac.new(
        settings.secret_key.encode("utf-8"), session_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_csrf_token(session_id: str, token: str) -> bool:
    """Сравнение CSRF-токенов в постоянном времени."""
    expected = sign_csrf_token(session_id)
    return hmac.compare_digest(expected, token or "")
