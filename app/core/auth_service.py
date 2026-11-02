"""Сервис аутентификации: регистрация, вход, refresh-ротация, сброс пароля.

Refresh-токены хранятся в БД в виде SHA-256 хэша и ротируются при обновлении
(одноразовые). Это позволяет отзывать сессии (logout, смена пароля) и
обнаруживать повторное использование токена.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.mail import send_password_reset
from app.core.security import (
    create_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.logging_config import get_logger
from app.models.user import PasswordResetToken, RefreshToken, User

logger = get_logger(__name__)


class AuthError(HTTPException):
    """Ошибка аутентификации с безопасным текстом (без утечки деталей)."""

    def __init__(self, detail: str = "Неверный логин или пароль") -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    """Ищет пользователя по логину (без учёта регистра)."""
    result = await db.execute(select(User).where(User.username == username.strip().lower()))
    return result.scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Ищет пользователя по email."""
    result = await db.execute(select(User).where(User.email == email.strip().lower()))
    return result.scalar_one_or_none()


async def register_user(
    db: AsyncSession,
    username: str,
    password: str,
    email: str | None,
    biometric_consent: bool,
) -> User:
    """Создаёт пользователя. Проверяет уникальность логина и email."""
    if not biometric_consent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Требуется согласие на обработку биометрии",
        )

    if await get_user_by_username(db, username):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Логин уже занят")

    normalized_email = email.strip().lower() if email else None
    if normalized_email and await get_user_by_email(db, normalized_email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email уже используется")

    user = User(
        username=username.strip().lower(),
        email=normalized_email,
        password_hash=hash_password(password),
        display_name=username.strip(),
        biometric_consent_at=datetime.now(UTC),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("auth.registered", user_id=user.id, username=user.username)
    return user


async def authenticate(db: AsyncSession, username: str, password: str) -> User:
    """Проверяет логин/пароль. Не раскрывает, что именно неверно."""
    user = await get_user_by_username(db, username)
    if user is None:
        # Выполняем «фиктивную» проверку, чтобы выровнять время ответа (anti-timing).
        verify_password(password, hash_password("dummy-timing-equalizer"))
        raise AuthError()

    if not verify_password(password, user.password_hash):
        raise AuthError()

    if user.is_banned:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Аккаунт заблокирован")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Аккаунт отключён")

    return user


async def issue_tokens(
    db: AsyncSession, user: User, user_agent: str | None = None, ip: str | None = None
) -> tuple[str, str]:
    """Выдаёт пару (access, refresh) и сохраняет хэш refresh в БД."""
    access = create_token(user.id, "access", extra_claims={"adm": user.is_admin})

    raw_refresh = generate_opaque_token()
    expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(raw_refresh),
            expires_at=expires_at,
            user_agent=(user_agent or "")[:255] or None,
            ip_address=(ip or "")[:64] or None,
        )
    )
    await db.commit()
    return access, raw_refresh


async def rotate_refresh_token(
    db: AsyncSession, raw_refresh: str, user_agent: str | None = None, ip: str | None = None
) -> tuple[str, str, User]:
    """Ротация refresh-токена: старый отзывается, выдаётся новая пара.

    Если токен не найден/отозван/истёк — 401. Повторное использование
    отозванного токена трактуется как компрометация: отзываем все сессии.
    """
    token_hash = hash_token(raw_refresh)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()
    if stored is None:
        raise AuthError("Сессия не найдена, войдите заново")

    now = datetime.now(UTC)
    expires_at = stored.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if stored.revoked_at is not None:
        # Признак кражи: гасим все refresh-токены пользователя.
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == stored.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await db.commit()
        logger.warning("auth.refresh.reuse_detected", user_id=stored.user_id)
        raise AuthError("Сессия недействительна, войдите заново")

    if expires_at <= now:
        raise AuthError("Сессия истекла, войдите заново")

    user = await db.get(User, stored.user_id)
    if user is None or user.is_banned or not user.is_active:
        raise AuthError("Аккаунт недоступен")

    stored.revoked_at = now
    await db.flush()
    access, refresh = await issue_tokens(db, user, user_agent, ip)
    logger.info("auth.refresh.rotated", user_id=user.id)
    return access, refresh, user


async def revoke_refresh_token(db: AsyncSession, raw_refresh: str) -> None:
    """Отзывает конкретный refresh-токен (logout). Идемпотентно."""
    token_hash = hash_token(raw_refresh)
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == token_hash, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()


async def revoke_all_user_tokens(db: AsyncSession, user_id: int) -> None:
    """Отзывает все refresh-токены пользователя (смена пароля, удаление)."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()


async def create_password_reset(db: AsyncSession, email: str) -> str | None:
    """Создаёт токен сброса пароля и отправляет письмо со ссылкой.

    Возвращает raw-токен, если пользователь найден (для тестов); иначе None.
    Наружу всегда отвечаем одинаково, чтобы не раскрывать наличие email.
    """
    user = await get_user_by_email(db, email)
    if user is None:
        logger.info("auth.reset.requested_unknown_email")
        return None

    raw = generate_opaque_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        )
    )
    await db.commit()

    reset_url = f"{settings.base_url}/reset-password?token={raw}"
    # Пользователь найден по email, поэтому адрес не пустой; проверка нужна
    # для типизации, поле в модели допускает NULL.
    if user.email:
        sent = await send_password_reset(user.email, reset_url)
        if not sent:
            # SMTP не настроен или упал: ссылка в логе — единственный способ
            # завершить сброс, поэтому пишем её с явной пометкой.
            logger.info("auth.reset.email_stub", user_id=user.id, url=reset_url)
    return raw


async def confirm_password_reset(db: AsyncSession, token: str, new_password: str) -> None:
    """Устанавливает новый пароль по токену, отзывая все активные сессии."""
    token_hash = hash_token(token)
    result = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )
    stored = result.scalar_one_or_none()
    if stored is None or stored.used_at is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Токен недействителен")

    expires_at = stored.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Токен истёк")

    user = await db.get(User, stored.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Токен недействителен")

    user.password_hash = hash_password(new_password)
    stored.used_at = datetime.now(UTC)
    await db.flush()
    await revoke_all_user_tokens(db, user.id)
    logger.info("auth.reset.completed", user_id=user.id)


async def delete_account(db: AsyncSession, user: User) -> None:
    """GDPR-подобное удаление: пользователь и все связанные данные (каскад) + файлы."""
    from app.core.storage import delete_media
    from app.models.photo import Photo

    photos = (await db.execute(select(Photo).where(Photo.user_id == user.id))).scalars().all()
    for photo in photos:
        delete_media(photo.storage_path)

    await db.execute(delete(User).where(User.id == user.id))
    await db.commit()
    logger.info("auth.account.deleted", user_id=user.id)
