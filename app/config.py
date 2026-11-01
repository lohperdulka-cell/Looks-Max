"""Конфигурация приложения.

Все значения читаются из переменных окружения / .env через pydantic-settings.
Секреты (JWT_SECRET, ключи LLM, DSN БД) НИКОГДА не хардкодятся — только .env.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Настройки приложения. Значения по умолчанию рассчитаны на локальный запуск на SQLite."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Общее ---
    app_name: str = "FaceMetric"
    app_env: str = Field(default="local", description="local | staging | production")
    debug: bool = True
    base_url: str = "http://localhost:8000"
    secret_key: str = Field(
        default="dev-insecure-secret-change-me",
        description="Общий секрет (CSRF, подпись cookie). Обязателен в production.",
    )

    # --- База данных ---
    # SQLite по умолчанию для локального запуска; в prod — postgresql+asyncpg://...
    database_url: str = "sqlite+aiosqlite:///./data/facemetric.db"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # --- JWT / auth ---
    jwt_secret: str = Field(default="dev-jwt-secret-change-me")
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7
    cookie_secure: bool = False  # в prod за HTTPS выставить True
    cookie_domain: str | None = None
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # --- Rate limiting ---
    rate_limit_enabled: bool = True
    rate_limit_default: str = "120/minute"
    rate_limit_auth: str = "10/minute"

    # --- Файлы / загрузка ---
    media_root: Path = BASE_DIR / "data" / "media"
    max_upload_mb: int = 12
    allowed_image_types: tuple[str, ...] = ("image/jpeg", "image/png", "image/webp")

    # --- ML ---
    ml_enabled: bool = True
    ml_provider_primary: str = "insightface"
    ml_provider_fallback: str = "mediapipe"
    ml_model_dir: Path = BASE_DIR / "data" / "models"
    ml_lazy_load: bool = True

    # --- LLM (ИИ-консультант) ---
    llm_enabled: bool = False
    llm_provider: str = "openai"  # openai | openrouter | anthropic | openai_compatible
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 60.0
    llm_max_tokens: int = 1200

    # --- Внешние сервисы (опционально) ---
    sentry_dsn: str = ""
    plausible_domain: str = ""
    plausible_script_url: str = "https://plausible.io/js/script.js"

    # --- Почта (заглушка, SMTP подключается позже) ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = "no-reply@facemetric.local"

    # --- Прочее ---
    onboarding_version: int = 1
    weekly_battle_min_votes: int = 5

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        """Приводит sync-URL postgres к async-драйверу asyncpg."""
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Кэшированный доступ к настройкам (единый экземпляр на процесс)."""
    return Settings()


settings = get_settings()
