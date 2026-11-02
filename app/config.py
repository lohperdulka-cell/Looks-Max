"""Конфигурация приложения.

Все значения читаются из переменных окружения / .env через pydantic-settings.
Секреты (JWT_SECRET, ключи LLM, DSN БД) НИКОГДА не хардкодятся — только .env.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
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
    # Дополнительные origin, которым разрешены мутирующие запросы с cookie.
    # Нужны, если фронт отдаётся с другого хоста (например, CDN + api-домен).
    extra_allowed_origins: tuple[str, ...] = ()

    # --- Rate limiting ---
    rate_limit_enabled: bool = True
    rate_limit_default: str = "120/minute"
    rate_limit_auth: str = "10/minute"
    # Доверять ли X-Forwarded-For. Включать только когда перед приложением
    # стоит свой прокси (Render/nginx): тогда IP берётся из заголовка и лимиты
    # считаются по реальному клиенту. Напрямую с интернета оставлять False —
    # иначе клиент подделает XFF и обойдёт лимиты.
    trust_proxy_headers: bool = False

    # --- Файлы / загрузка ---
    media_root: Path = BASE_DIR / "data" / "media"
    max_upload_mb: int = 12
    # Лимит по числу пикселей, а не по размеру файла: PNG/JPEG сжимаются, и
    # файл на пару килобайт может развернуться в сотни мегабайт RGB при
    # декодировании (decompression bomb). Портрет не бывает больше 24 Мп.
    max_image_pixels: int = 24_000_000
    allowed_image_types: tuple[str, ...] = ("image/jpeg", "image/png", "image/webp")

    # --- ML ---
    ml_enabled: bool = True
    # MediaPipe основной: не тянет 280 МБ весов из сети, ~240 МБ RSS и 478
    # точек вместо 106. InsightFace — резерв (точнее по антропометрии и даёт
    # пол/возраст), но требует обрезки моделей, см. ml_insightface_modules.
    ml_provider_primary: str = "mediapipe"
    ml_provider_fallback: str = "insightface"
    # Какие модели buffalo_l реально грузить. Полный набор — 5 моделей и пик
    # ~920 МБ RSS (OOM на 512 МБ). Коду нужны только эти три: детектор, 106
    # точек и пол/возраст. 1k3d68 (137 МБ) и w600k_r50 (166 МБ) не используются
    # и именно они дают скачок памяти при создании ONNX-сессий.
    # allowed_modules у InsightFace НЕ помогает: сессия создаётся до фильтра.
    ml_insightface_modules: tuple[str, ...] = (
        "detection",
        "landmark_2d_106",
        "genderage",
    )
    # Демо-провайдер (синтетическая сетка лица) — последний резерв для деплоя
    # без InsightFace/MediaPipe. Не измеряет реальное лицо: только проверка
    # работоспособности конвейера. Помечается флагом is_demo в ответах.
    ml_provider_demo: bool = False
    ml_model_dir: Path = BASE_DIR / "data" / "models"
    ml_lazy_load: bool = True
    # MediaPipe 0.10+/1.x использует Tasks API и требует .task-модель.
    ml_mediapipe_allow_download: bool = True
    ml_mediapipe_model_url: str = (
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
        "face_landmarker/float16/1/face_landmarker.task"
    )

    # --- LLM (ИИ-консультант) ---
    llm_enabled: bool = False
    llm_provider: str = "openai"  # openai | openrouter | anthropic | openai_compatible
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 60.0
    llm_max_tokens: int = 1200
    # Сколько последних сообщений диалога уходит в промпт. Без потолка сессия
    # растёт бесконечно и каждый новый вопрос отправлял бы всю историю —
    # линейный рост стоимости запроса к LLM.
    llm_history_messages: int = 12

    # --- Внешние сервисы (опционально) ---
    sentry_dsn: str = ""
    plausible_domain: str = ""
    plausible_script_url: str = "https://plausible.io/js/script.js"

    # --- Почта ---
    # Без smtp_host письма не отправляются, ссылка сброса уходит в лог.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    smtp_timeout_seconds: float = 15.0
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

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        """Origin'ы, которым разрешены запросы с cookie: свой + дополнительные.

        Единый список для CORS и для CSRF-проверки, чтобы они не разошлись:
        если origin разрешён в CORS, но не в CSRF-гварде (или наоборот), фронт
        либо получит 403, либо дыру в защите.
        """
        own = (self.base_url.rstrip("/"),)
        extra = tuple(o.rstrip("/") for o in self.extra_allowed_origins if o.strip())
        return tuple(dict.fromkeys(own + extra))

    @model_validator(mode="after")
    def _guard_production_secrets(self) -> Settings:
        """Не даёт поднять прод на дефолтных секретах.

        Значения по умолчанию лежат в репозитории, поэтому с ними любой может
        подделать JWT и cookie. Падаем на старте: молчаливый запуск с публично
        известным секретом хуже, чем неудачный деплой.
        """
        if not self.is_production:
            return self

        insecure = {
            "SECRET_KEY": "dev-insecure-secret-change-me",
            "JWT_SECRET": "dev-jwt-secret-change-me",
        }
        used_default = [
            name for name, default in insecure.items() if getattr(self, name.lower()) == default
        ]
        if used_default:
            raise ValueError("в production задайте свои значения: " + ", ".join(used_default))
        if not self.cookie_secure:
            raise ValueError("в production требуется COOKIE_SECURE=true")
        return self


@lru_cache
def get_settings() -> Settings:
    """Кэшированный доступ к настройкам (единый экземпляр на процесс)."""
    return Settings()


settings = get_settings()
