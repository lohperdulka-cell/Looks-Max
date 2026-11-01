"""Роутер аутентификации: регистрация, вход, refresh, logout, сброс пароля.

Токены кладутся в httpOnly cookies (access + refresh) и дублируются в теле ответа
для API-клиентов. CSRF-токен — в читаемой cookie fm_csrf, JS присылает его в
заголовке X-CSRF-Token для мутирующих запросов.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Cookie, Header, Request, Response, status
from fastapi.responses import JSONResponse

from app.config import settings
from app.core import auth_service
from app.core.deps import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE, CurrentUser, DbSession
from app.core.limiter import limiter
from app.core.security import sign_csrf_token
from app.logging_config import get_logger
from app.schemas.auth import (
    LoginRequest,
    MessageResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterRequest,
    TokenResponse,
    UserMe,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    """Ставит httpOnly cookies с access/refresh и CSRF-cookie (читаемую)."""
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        max_age=settings.access_token_ttl_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=settings.refresh_token_ttl_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        path="/",
    )
    # CSRF-токен должен быть доступен JS — httponly=False.
    csrf_value = secrets.token_urlsafe(24)
    response.set_cookie(
        CSRF_COOKIE,
        csrf_value,
        max_age=settings.refresh_token_ttl_days * 86400,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        path="/",
    )
    response.headers["X-CSRF-Token"] = sign_csrf_token(csrf_value)


def _clear_auth_cookies(response: Response) -> None:
    for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/", domain=settings.cookie_domain)


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=TokenResponse)
@limiter.limit(settings.rate_limit_auth)
async def register(request: Request, payload: RegisterRequest, db: DbSession) -> JSONResponse:
    """Регистрация по логину и паролю с согласием на обработку биометрии."""
    user = await auth_service.register_user(
        db, payload.username, payload.password, payload.email, payload.biometric_consent
    )
    access, refresh = await auth_service.issue_tokens(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    body = TokenResponse(access_token=access, refresh_token=refresh).model_dump()
    response = JSONResponse(status_code=status.HTTP_201_CREATED, content=body)
    _set_auth_cookies(response, access, refresh)
    return response


@router.post("/login", response_model=TokenResponse)
@limiter.limit(settings.rate_limit_auth)
async def login(request: Request, payload: LoginRequest, db: DbSession) -> JSONResponse:
    """Вход по логину и паролю."""
    user = await auth_service.authenticate(db, payload.username, payload.password)
    access, refresh = await auth_service.issue_tokens(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    body = TokenResponse(access_token=access, refresh_token=refresh).model_dump()
    response = JSONResponse(status_code=status.HTTP_200_OK, content=body)
    _set_auth_cookies(response, access, refresh)
    logger.info("auth.login", user_id=user.id)
    return response


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    request: Request,
    db: DbSession,
    fm_refresh: Annotated[str | None, Cookie()] = None,
    x_refresh_token: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Обновляет access-токен по refresh (с ротацией refresh)."""
    raw = fm_refresh or x_refresh_token
    if not raw:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Нет refresh-токена"}
        )
    access, refresh, _user = await auth_service.rotate_refresh_token(
        db,
        raw,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    body = TokenResponse(access_token=access, refresh_token=refresh).model_dump()
    response = JSONResponse(status_code=status.HTTP_200_OK, content=body)
    _set_auth_cookies(response, access, refresh)
    return response


@router.post("/logout", response_model=MessageResponse)
async def logout(
    db: DbSession,
    fm_refresh: Annotated[str | None, Cookie()] = None,
) -> JSONResponse:
    """Выход: отзывает refresh-токен и очищает cookies."""
    if fm_refresh:
        await auth_service.revoke_refresh_token(db, fm_refresh)
    response = JSONResponse(
        status_code=status.HTTP_200_OK, content={"detail": "Вы вышли из аккаунта"}
    )
    _clear_auth_cookies(response)
    return response


@router.get("/me", response_model=UserMe)
async def me(user: CurrentUser) -> UserMe:
    """Текущий пользователь (владелец аккаунта)."""
    return UserMe.model_validate(user)


@router.post("/password-reset", response_model=MessageResponse)
@limiter.limit(settings.rate_limit_auth)
async def request_password_reset(
    request: Request, payload: PasswordResetRequest, db: DbSession
) -> MessageResponse:
    """Запрос сброса пароля. Ответ одинаков независимо от наличия email."""
    await auth_service.create_password_reset(db, payload.email)
    return MessageResponse(detail="Если такой email зарегистрирован, письмо со ссылкой отправлено")


@router.post("/password-reset/confirm", response_model=MessageResponse)
async def confirm_password_reset(payload: PasswordResetConfirm, db: DbSession) -> MessageResponse:
    """Устанавливает новый пароль по токену из письма."""
    await auth_service.confirm_password_reset(db, payload.token, payload.new_password)
    return MessageResponse(detail="Пароль обновлён, войдите заново")


@router.delete("/account", response_model=MessageResponse)
async def delete_account(user: CurrentUser, db: DbSession) -> JSONResponse:
    """Удаляет аккаунт и все связанные данные (GDPR-подобно)."""
    user_id = user.id
    await auth_service.delete_account(db, user)
    logger.info("auth.account.deleted.endpoint", user_id=user_id)
    response = JSONResponse(status_code=status.HTTP_200_OK, content={"detail": "Аккаунт удалён"})
    _clear_auth_cookies(response)
    return response
