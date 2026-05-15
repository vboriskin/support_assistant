"""Конфигурация приложения через pydantic-settings.

Все настройки читаются из `.env` (путь можно переопределить переменной
окружения `ENV_FILE`). Группы настроек устроены как вложенные модели —
каждая со своим `env_prefix`, чтобы исключить коллизии имён.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = os.getenv("ENV_FILE", ".env")


class DBSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DB_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    backend: Literal["sqlite", "postgres"] = "sqlite"

    sqlite_path: Path = Path("./data/app.db")

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "support_assistant"
    postgres_user: str = "sa_user"
    postgres_password: SecretStr = SecretStr("")
    postgres_pool_size: int = 10
    postgres_max_overflow: int = 5

    @property
    def url(self) -> str:
        if self.backend == "sqlite":
            return f"sqlite+aiosqlite:///{self.sqlite_path.absolute()}"
        pw = self.postgres_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{pw}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_url(self) -> str:
        """Синхронный URL для Alembic-миграций."""
        if self.backend == "sqlite":
            return f"sqlite:///{self.sqlite_path.absolute()}"
        pw = self.postgres_password.get_secret_value()
        return (
            f"postgresql+psycopg://{self.postgres_user}:{pw}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


class GigaChatSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GIGACHAT_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    base_url: str = "https://gigachat.devices.sberbank.ru/api/v1"
    auth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    # Готовый Bearer-токен (если задан — OAuth-обмен не делается; используется
    # как fast-path для тестов и когда токен внешне выпущен). См. также
    # INTEGRATIONS_PORTING_GUIDE: «token ИЛИ clientId+clientSecret».
    access_token: SecretStr = SecretStr("")
    client_id: SecretStr = SecretStr("")
    client_secret: SecretStr = SecretStr("")
    scope: str = "GIGACHAT_API_CORP"
    model_primary: str = "GigaChat-Max"
    model_judge: str = "GigaChat-Max"
    verify_ssl: bool = False
    ca_bundle_path: Path | None = None


class YandexGPTSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YANDEX_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    api_key: SecretStr = SecretStr("")
    folder_id: str = ""
    model_uri: str = ""


class OpenAICompatSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPENAI_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    base_url: str = "http://localhost:8080/v1"
    api_key: SecretStr = SecretStr("dummy")
    model: str = "Qwen2.5-32B-Instruct"


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    provider: Literal["gigachat", "yandexgpt", "openai_compatible", "mock"] = "mock"
    timeout_seconds: int = 60
    max_retries: int = 3
    budget_per_user_daily: int = 300


class EmbeddingsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EMBEDDINGS_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    provider: Literal["local", "api", "mock"] = "local"
    model_name: str = "intfloat/multilingual-e5-large"
    cache_dir: Path = Path("./models/embeddings")
    device: Literal["cpu", "cuda", "mps"] = "cpu"
    batch_size: int = 32
    dimension: int = 1024
    api_url: str | None = None
    api_key: SecretStr = SecretStr("")


class VectorStoreSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    backend: Literal["sqlite_vec", "pgvector", ""] = Field(default="", alias="VECTOR_BACKEND")
    search_top_k: int = Field(default=30, alias="VECTOR_SEARCH_TOP_K")
    text_search_top_k: int = Field(default=30, alias="TEXT_SEARCH_TOP_K")


class RetrievalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RETRIEVAL_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    rrf_k: int = 60
    final_top_k: int = 8


class RerankerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RERANKER_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    enabled: bool = True
    type: Literal["llm", "cross_encoder", "none"] = "llm"
    model: str | None = None


class PIISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PII_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    enabled: bool = True
    ner_enabled: bool = True
    strict_mode: bool = True
    audit_sample_rate: float = 0.1


class IngestSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INGEST_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    batch_size: int = 50
    llm_concurrency: int = 4
    max_ticket_age_days: int = 540


class SecuritySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SECURITY_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    rate_limit_per_minute: int = 120
    max_body_bytes: int = 10 * 1024 * 1024
    csrf_enabled: bool = True
    allowed_llm_hosts: str = ""
    db_audit_enabled: bool = True


class AlertSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ALERTS_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    enabled: bool = False
    webhook_url: str = ""
    p95_latency_threshold_ms: int = 5000
    no_sources_ratio_threshold: float = 0.4
    error_count_threshold: int = 10
    check_interval_sec: int = 300


class IntegrationsSettings(BaseSettings):
    """Общие настройки для внешних интеграций (LLM, embeddings, webhooks, SM)."""

    model_config = SettingsConfigDict(
        env_prefix="INTEGRATIONS_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    verify_ssl: bool = True
    http_timeout_sec: int = 30
    proxy_url: str = ""


class UISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="UI_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    default_theme: Literal["dark", "light", "auto"] = "auto"
    show_debug_panel: bool = False


class Settings(BaseSettings):
    """Корневые настройки приложения."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["local", "dev", "prod"] = "local"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"
    cors_allowed_origins: str = "http://localhost:8000"

    db: DBSettings = Field(default_factory=DBSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    gigachat: GigaChatSettings = Field(default_factory=GigaChatSettings)
    yandexgpt: YandexGPTSettings = Field(default_factory=YandexGPTSettings)
    openai_compat: OpenAICompatSettings = Field(default_factory=OpenAICompatSettings)
    embeddings: EmbeddingsSettings = Field(default_factory=EmbeddingsSettings)
    vector_store: VectorStoreSettings = Field(default_factory=VectorStoreSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)
    pii: PIISettings = Field(default_factory=PIISettings)
    ingest: IngestSettings = Field(default_factory=IngestSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    integrations: IntegrationsSettings = Field(default_factory=IntegrationsSettings)
    ui: UISettings = Field(default_factory=UISettings)


_settings: Settings | None = None


def get_settings() -> Settings:
    """Singleton-доступ к настройкам."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    """Сброс кэша — нужен в тестах при изменении окружения."""
    global _settings
    _settings = None
