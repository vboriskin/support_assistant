# 04. Configuration

## Принципы

1. Вся конфигурация — через `.env` файл и `pydantic-settings`. Дефолты — в Python-коде, который читает env-переменные.
2. Никаких хардкодов URL, ключей, путей в основном коде.
3. `.env` — в `.gitignore`. В репозитории — `.env.example` с шаблоном.
4. На разных окружениях — разные `.env` файлы. Локально — `.env.local`, в контуре — `.env.prod` (или просто `.env`).
5. Переключение между SQLite и Postgres — одной переменной.
6. Переключение между провайдерами LLM — одной переменной.

## Файл `.env.example`

Это шаблон, который кладётся в репозиторий. Реальный `.env` создаётся копированием и подстановкой значений.

```bash
# ============================================================
# Общие
# ============================================================
APP_ENV=local                       # local | dev | prod
APP_HOST=127.0.0.1
APP_PORT=8000
APP_BASE_URL=http://localhost:8000
LOG_LEVEL=INFO                      # DEBUG | INFO | WARNING | ERROR
LOG_FORMAT=console                  # console | json (json для prod)
CORS_ALLOWED_ORIGINS=http://localhost:8000

# ============================================================
# Database
# ============================================================
# db_backend: sqlite | postgres
DB_BACKEND=sqlite

# Для SQLite
SQLITE_PATH=./data/app.db

# Для Postgres
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=support_assistant
POSTGRES_USER=sa_user
POSTGRES_PASSWORD=change_me
POSTGRES_POOL_SIZE=10
POSTGRES_MAX_OVERFLOW=5

# ============================================================
# LLM
# ============================================================
# llm_provider: gigachat | yandexgpt | openai_compatible | mock
LLM_PROVIDER=gigachat
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=3
LLM_BUDGET_PER_USER_DAILY=300       # бюджет запросов на пользователя в сутки

# --- GigaChat ---
GIGACHAT_BASE_URL=https://gigachat.devices.sberbank.ru/api/v1
GIGACHAT_AUTH_URL=https://ngw.devices.sberbank.ru:9443/api/v2/oauth
GIGACHAT_CLIENT_ID=
GIGACHAT_CLIENT_SECRET=
GIGACHAT_SCOPE=GIGACHAT_API_CORP
GIGACHAT_MODEL_PRIMARY=GigaChat-Max
GIGACHAT_MODEL_JUDGE=GigaChat-Max
GIGACHAT_VERIFY_SSL=false           # false для self-signed внутри корп-сети
GIGACHAT_CA_BUNDLE_PATH=            # путь к CA-bundle, если есть

# --- YandexGPT (опционально) ---
YANDEX_API_KEY=
YANDEX_FOLDER_ID=
YANDEX_MODEL_URI=gpt://b1g.../yandexgpt/latest

# --- OpenAI-compatible (для локальной модели/dev) ---
OPENAI_BASE_URL=http://localhost:8080/v1
OPENAI_API_KEY=dummy
OPENAI_MODEL=Qwen2.5-32B-Instruct

# ============================================================
# Embeddings
# ============================================================
# embeddings_provider: local | api
EMBEDDINGS_PROVIDER=local
EMBEDDINGS_MODEL_NAME=intfloat/multilingual-e5-large
EMBEDDINGS_CACHE_DIR=./models/embeddings
EMBEDDINGS_DEVICE=cpu               # cpu | cuda | mps
EMBEDDINGS_BATCH_SIZE=32
EMBEDDINGS_DIMENSION=1024           # должно совпадать с моделью

# Для api-provider
EMBEDDINGS_API_URL=
EMBEDDINGS_API_KEY=

# ============================================================
# Vector Store
# ============================================================
# vector_store_backend: sqlite_vec | pgvector
# на старте автоматически выбирается по DB_BACKEND, можно переопределить:
VECTOR_STORE_BACKEND=               # пусто = авто
VECTOR_SEARCH_TOP_K=30              # сколько брать из векторного поиска
TEXT_SEARCH_TOP_K=30                # сколько брать из FTS

# ============================================================
# Retrieval / Reranking
# ============================================================
RETRIEVAL_RRF_K=60                  # параметр Reciprocal Rank Fusion
RETRIEVAL_FINAL_TOP_K=8             # итоговое количество источников в промпте
RERANKER_ENABLED=true
RERANKER_TYPE=llm                   # llm | cross_encoder | none
RERANKER_MODEL=                     # если cross_encoder

# ============================================================
# PII
# ============================================================
PII_ENABLED=true
PII_NER_ENABLED=true                # natasha NER (медленнее, но точнее)
PII_STRICT_MODE=true                # любая пропущенная PII = ошибка пайплайна
PII_AUDIT_SAMPLE_RATE=0.1           # доля тикетов для ручной проверки

# ============================================================
# Ingest
# ============================================================
INGEST_BATCH_SIZE=50                # сколько тикетов обрабатывать параллельно
INGEST_LLM_CONCURRENCY=4            # одновременные LLM-вызовы
INGEST_MAX_TICKET_AGE_DAYS=540      # старше — не индексируем

# ============================================================
# Безопасность
# ============================================================
SECURITY_RATE_LIMIT_PER_MINUTE=120  # на IP/пользователя
SECURITY_MAX_BODY_BYTES=10485760    # 10 МБ
SECURITY_CSRF_ENABLED=true
SECURITY_ALLOWED_LLM_HOSTS=         # доп. хосты для LLM кроме дефолтов

# ============================================================
# Frontend
# ============================================================
UI_DEFAULT_THEME=auto               # dark | light | auto
UI_SHOW_DEBUG_PANEL=false           # показывать ли источники debug-инфой
```

## Settings.py

Реализация на pydantic-settings v2. Структура с вложенными моделями для группировки.

```python
# config/settings.py
from pathlib import Path
from typing import Literal
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DBSettings(BaseSettings):
    """Настройки БД. Автоматически выбирает SQLite или Postgres."""
    model_config = SettingsConfigDict(env_prefix="DB_")

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
        """Возвращает SQLAlchemy URL."""
        if self.backend == "sqlite":
            return f"sqlite+aiosqlite:///{self.sqlite_path.absolute()}"
        pw = self.postgres_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{pw}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


class GigaChatSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GIGACHAT_")
    base_url: str = "https://gigachat.devices.sberbank.ru/api/v1"
    auth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    client_id: SecretStr = SecretStr("")
    client_secret: SecretStr = SecretStr("")
    scope: str = "GIGACHAT_API_CORP"
    model_primary: str = "GigaChat-Max"
    model_judge: str = "GigaChat-Max"
    verify_ssl: bool = False
    ca_bundle_path: Path | None = None


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_")
    provider: Literal["gigachat", "yandexgpt", "openai_compatible", "mock"] = "gigachat"
    timeout_seconds: int = 60
    max_retries: int = 3
    budget_per_user_daily: int = 300


class EmbeddingsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EMBEDDINGS_")
    provider: Literal["local", "api"] = "local"
    model_name: str = "intfloat/multilingual-e5-large"
    cache_dir: Path = Path("./models/embeddings")
    device: Literal["cpu", "cuda", "mps"] = "cpu"
    batch_size: int = 32
    dimension: int = 1024
    api_url: str | None = None
    api_key: SecretStr = SecretStr("")


class VectorStoreSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VECTOR_")
    backend: Literal["sqlite_vec", "pgvector", ""] = ""    # "" = авто
    search_top_k: int = Field(default=30, alias="vector_search_top_k")
    text_search_top_k: int = Field(default=30, alias="text_search_top_k")


class RetrievalSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RETRIEVAL_")
    rrf_k: int = 60
    final_top_k: int = 8


class RerankerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RERANKER_")
    enabled: bool = True
    type: Literal["llm", "cross_encoder", "none"] = "llm"
    model: str | None = None


class PIISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PII_")
    enabled: bool = True
    ner_enabled: bool = True
    strict_mode: bool = True
    audit_sample_rate: float = 0.1


class IngestSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INGEST_")
    batch_size: int = 50
    llm_concurrency: int = 4
    max_ticket_age_days: int = 540


class SecuritySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SECURITY_")
    rate_limit_per_minute: int = 120
    max_body_bytes: int = 10 * 1024 * 1024
    csrf_enabled: bool = True
    allowed_llm_hosts: str = ""


class UISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UI_")
    default_theme: Literal["dark", "light", "auto"] = "auto"
    show_debug_panel: bool = False


class Settings(BaseSettings):
    """Корневые настройки приложения."""
    model_config = SettingsConfigDict(
        env_file=".env",
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

    # Группы настроек загружаются отдельно
    db: DBSettings = Field(default_factory=DBSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    gigachat: GigaChatSettings = Field(default_factory=GigaChatSettings)
    embeddings: EmbeddingsSettings = Field(default_factory=EmbeddingsSettings)
    vector_store: VectorStoreSettings = Field(default_factory=VectorStoreSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)
    pii: PIISettings = Field(default_factory=PIISettings)
    ingest: IngestSettings = Field(default_factory=IngestSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    ui: UISettings = Field(default_factory=UISettings)


_settings: Settings | None = None

def get_settings() -> Settings:
    """Singleton-доступ к настройкам."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
```

## Использование в коде

```python
# В adapter / service:
from config.settings import get_settings

settings = get_settings()
llm_client = create_llm_client(settings.llm, settings.gigachat)
```

В FastAPI — через зависимости:

```python
# api/dependencies.py
from fastapi import Depends
from functools import lru_cache
from config.settings import Settings, get_settings

@lru_cache
def settings_dep() -> Settings:
    return get_settings()

# В роутах:
from typing import Annotated
async def some_endpoint(settings: Annotated[Settings, Depends(settings_dep)]):
    ...
```

## Логика автовыбора vector_store_backend

В `adapters/vector_store/factory.py`:

```python
def get_vector_store(settings: Settings) -> VectorStore:
    backend = settings.vector_store.backend
    if not backend:
        # авто: по DB_BACKEND
        backend = "pgvector" if settings.db.backend == "postgres" else "sqlite_vec"
    if backend == "sqlite_vec":
        return SQLiteVecStore(settings)
    if backend == "pgvector":
        return PgVectorStore(settings)
    raise ValueError(f"Unknown vector store backend: {backend}")
```

Аналогично для text_search:

```python
def get_text_search(settings: Settings) -> TextSearch:
    if settings.db.backend == "postgres":
        return PostgresFTS(settings)
    return SQLiteFTS5(settings)
```

## Запуск с разными конфигами

```bash
# Локально (SQLite, всё в одной директории)
APP_ENV=local uvicorn api.main:app --reload

# С Postgres
DB_BACKEND=postgres uvicorn api.main:app

# С другим .env файлом
ENV_FILE=.env.prod uvicorn api.main:app
```

Для этого в `Settings` сделать чтение `ENV_FILE`:

```python
import os
_env_file = os.getenv("ENV_FILE", ".env")
# и передать в SettingsConfigDict(env_file=_env_file, ...)
```

## Валидация настроек при старте

В `api/main.py` lifespan: проверяем, что критичные настройки заданы.

```python
async def lifespan(app: FastAPI):
    settings = get_settings()
    # GigaChat client_id обязателен если provider=gigachat и не mock
    if settings.llm.provider == "gigachat":
        assert settings.gigachat.client_id.get_secret_value(), \
            "GIGACHAT_CLIENT_ID is required when LLM_PROVIDER=gigachat"
    # Создаём директории
    settings.db.sqlite_path.parent.mkdir(parents=True, exist_ok=True) \
        if settings.db.backend == "sqlite" else None
    settings.embeddings.cache_dir.mkdir(parents=True, exist_ok=True)
    yield
```
