"""FastAPI-зависимости: текущий пользователь (cookie/Bearer), админ, CSRF."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlparse

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import decode_token
from app.db import get_session
from app.models.user import User

ACCESS_COOKIE = "fm_access"
REFRESH_COOKIE = "fm_refresh"
CSRF_COOKIE = "fm_csrf"

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def _user_from_token(token: str | None, db: AsyncSession) -> User | None:
    if not token:
        return None
    try:
        payload = decode_token(token, expected_type="access")
    except jwt.PyJWTError:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    user = await db.get(User, int(user_id))
    return user


async def get_current_user_optional(
    db: DbSession,
    fm_access: Annotated[str | None, Cookie()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> User | None:
    """Возвращает пользователя, если он аутентифицирован, иначе None (для публичных страниц)."""
    token = fm_access
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    user = await _user_from_token(token, db)
    if user and (user.is_banned or not user.is_active):
        return None
    return user


async def get_current_user(
    user: Annotated[User | None, Depends(get_current_user_optional)],
) -> User:
    """Требует аутентификации; иначе 401."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется аутентификация",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Требует роль администратора; иначе 403."""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
    return user


# Методы, меняющие состояние: только для них проверяется Origin.
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _allowed_origins() -> set[str]:
    """Origin, с которых разрешены мутирующие запросы с cookie (кроме своего)."""
    return set(settings.allowed_origins)


def _request_origin(request: Request) -> str:
    """Собственный origin запроса по заголовку Host (схему за прокси не берём)."""
    return request.headers.get("host", "").rstrip("/")


def _origin_matches(origin: str, request: Request) -> bool:
    """True, если origin совпадает со своим хостом или с разрешённым списком.

    Свой origin вычисляется из Host запроса, а не только из BASE_URL: так
    same-origin работает при любом домене/порту, даже если BASE_URL в окружении
    не выставлен под текущий деплой. Браузер подставляет Host целевого сервера
    сам, поэтому подменить его из чужого сайта нельзя.
    """
    candidate = origin.rstrip("/")
    if not candidate:
        return False
    parsed = urlparse(candidate)
    # Сравниваем по host:port — схема за обратным прокси может отличаться.
    if parsed.netloc and parsed.netloc == _request_origin(request):
        return True
    return candidate in _allowed_origins()


async def csrf_origin_guard(request: Request) -> None:
    """Блокирует межсайтовые мутирующие запросы по заголовку Origin.

    httpOnly-cookie уходит в запрос автоматически, поэтому чужой сайт может
    дёрнуть наш API «руками» пользователя. CSRF-заголовок без этой проверки —
    защита только на бумаге: fetch() его не добавит, но и не мешает браузеру
    отправить саму cookie.

    Проверяем только Origin, а не Referer:
      * браузеры ВСЕГДА шлют Origin на cross-origin POST/PUT/PATCH/DELETE, а из
        JS подделать его нельзя — этого достаточно против CSRF;
      * Referer для нашего аналитического маячка штатно указывает на чужой сайт
        (переход по внешней ссылке) и является полезными данными, а не признаком
        атаки; блокировка по Referer сломала бы учёт переходов.

    Если Origin отсутствует, запрос пришёл не из браузера (curl, скрипты,
    серверные клиенты) — там CSRF неприменим, пропускаем. Безопасные методы
    (GET/HEAD/OPTIONS) не проверяются: они не меняют состояние.
    """
    if request.method not in _UNSAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is not None and not _origin_matches(origin, request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Запрос с чужого origin")


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]
CurrentAdmin = Annotated[User, Depends(get_current_admin)]
