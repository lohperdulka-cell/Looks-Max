"""Pydantic-схемы аутентификации и пользователя."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

USERNAME_MIN = 3
USERNAME_MAX = 32
PASSWORD_MIN = 8


class RegisterRequest(BaseModel):
    """Регистрация: логин + пароль, опционально email и согласие на биометрию."""

    username: str = Field(min_length=USERNAME_MIN, max_length=USERNAME_MAX)
    password: str = Field(min_length=PASSWORD_MIN, max_length=128)
    email: EmailStr | None = None
    biometric_consent: bool = Field(
        description="Явное согласие на обработку биометрических данных (фото лица)."
    )

    @field_validator("username")
    @classmethod
    def _validate_username(cls, v: str) -> str:
        v = v.strip()
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Логин может содержать только буквы, цифры, _ и -")
        return v.lower()

    @field_validator("password")
    @classmethod
    def _validate_password(cls, v: str) -> str:
        if v.strip() != v:
            raise ValueError("Пароль не должен начинаться/заканчиваться пробелами")
        if v.isdigit() or v.isalpha():
            raise ValueError("Пароль должен содержать буквы и цифры")
        return v

    @field_validator("biometric_consent")
    @classmethod
    def _require_consent(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Необходимо согласие на обработку биометрии")
        return v


class LoginRequest(BaseModel):
    """Вход по логину и паролю."""

    username: str = Field(min_length=1, max_length=USERNAME_MAX)
    password: str = Field(min_length=1, max_length=128)


class PasswordResetRequest(BaseModel):
    """Запрос восстановления пароля (по email)."""

    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Установка нового пароля по токену из письма."""

    token: str = Field(min_length=10)
    new_password: str = Field(min_length=PASSWORD_MIN, max_length=128)


class UserPublic(BaseModel):
    """Публичное представление пользователя (без чувствительных полей)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str | None = None
    avatar_path: str | None = None
    gender: str | None = None
    elo: int
    streak_days: int
    is_admin: bool
    created_at: datetime


class UserMe(UserPublic):
    """Расширенное представление для владельца аккаунта."""

    email: str | None = None
    show_in_battles: bool = False
    biometric_consent_at: datetime | None = None


class TokenResponse(BaseModel):
    """Ответ с токенами (для API-клиентов; в браузере используются cookies)."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class MessageResponse(BaseModel):
    """Простой текстовый ответ."""

    detail: str


class ProfileUpdate(BaseModel):
    """Обновление профиля."""

    display_name: str | None = Field(default=None, max_length=64)
    gender: str | None = Field(default=None, pattern="^(male|female|other)$")
    birth_year: int | None = Field(default=None, ge=1920, le=2020)
    show_in_battles: bool | None = None
