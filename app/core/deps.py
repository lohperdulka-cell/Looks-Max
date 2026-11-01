"""FastAPI-зависимости: текущий пользователь (cookie/Bearer), админ, CSRF."""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token, verify_csrf_token
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


async def verify_csrf(
    request: Request,
    fm_csrf: Annotated[str | None, Cookie()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> None:
    """Двойная проверка CSRF: cookie fm_csrf должен совпадать с заголовком X-CSRF-Token.

    Для мутирующих запросов из форм/JS. HMAC-подпись привязывает токен к secret_key.
    """
    header_token = x_csrf_token or request.headers.get("x-csrf-token")
    cookie_token = fm_csrf
    if not cookie_token or not header_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Отсутствует CSRF-токен")
    if not verify_csrf_token(cookie_token, header_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Неверный CSRF-токен")


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]
CurrentAdmin = Annotated[User, Depends(get_current_admin)]
CsrfGuard = Annotated[None, Depends(verify_csrf)]
