"""GET/PATCH /api/settings — UI «Настройки».

GET возвращает структурированный список настроек: значения, env-имя, тип,
описание, требуется ли перезапуск.

PATCH принимает словарь {env_name: value}, записывает изменения в .env (или
создаёт его рядом с проектом). После записи отдаёт ``restart_required: true``
— UI просит пользователя перезапустить uvicorn.

Запись ограничена белым списком ключей (см. ``_FIELDS``). Это защита от
произвольной модификации файла.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import get_user_id, settings_dep
from config.logging import get_logger
from config.settings import Settings

logger = get_logger("api.settings")
router = APIRouter(prefix="/settings", tags=["settings"])

_ENV_FILE = Path(os.getenv("ENV_FILE", ".env"))


SettingType = Literal["bool", "int", "float", "str", "secret", "enum"]


class FieldSpec(BaseModel):
    name: str
    type: SettingType
    description: str = ""
    options: list[str] | None = None   # для enum
    secret: bool = False               # не светить значение в UI
    restart_required: bool = True


class GroupSpec(BaseModel):
    id: str
    title: str
    description: str = ""
    fields: list[FieldSpec]


_GROUPS: list[GroupSpec] = [
    GroupSpec(
        id="general", title="Общее",
        fields=[
            FieldSpec(name="APP_ENV", type="enum", options=["local", "dev", "prod"], description="Окружение"),
            FieldSpec(name="APP_HOST", type="str", description="Хост uvicorn"),
            FieldSpec(name="APP_PORT", type="int", description="Порт uvicorn"),
            FieldSpec(name="LOG_LEVEL", type="enum", options=["DEBUG", "INFO", "WARNING", "ERROR"], description="Уровень логирования"),
            FieldSpec(name="LOG_FORMAT", type="enum", options=["console", "json"], description="Формат логов"),
        ],
    ),
    GroupSpec(
        id="db", title="База данных",
        fields=[
            FieldSpec(name="DB_BACKEND", type="enum", options=["sqlite", "postgres"], description="Бэкенд БД"),
            FieldSpec(name="DB_SQLITE_PATH", type="str", description="Файл SQLite"),
            FieldSpec(name="DB_POSTGRES_HOST", type="str", description="Postgres host"),
            FieldSpec(name="DB_POSTGRES_PORT", type="int", description="Postgres port"),
            FieldSpec(name="DB_POSTGRES_DB", type="str", description="Postgres database name"),
            FieldSpec(name="DB_POSTGRES_USER", type="str", description="Postgres user"),
            FieldSpec(name="DB_POSTGRES_PASSWORD", type="secret", secret=True, description="Postgres password"),
        ],
    ),
    GroupSpec(
        id="llm", title="LLM",
        description="Какой LLM-провайдер ассистент использует и его настройки.",
        fields=[
            FieldSpec(name="LLM_PROVIDER", type="enum", options=["mock", "gigachat", "yandexgpt", "openai_compatible"], description="Провайдер"),
            FieldSpec(name="LLM_TIMEOUT_SECONDS", type="int", description="Таймаут одного вызова"),
            FieldSpec(name="LLM_MAX_RETRIES", type="int", description="Сколько раз повторять при сбое"),
            FieldSpec(name="LLM_BUDGET_PER_USER_DAILY", type="int", description="Лимит вызовов на пользователя в сутки"),

            # GigaChat
            FieldSpec(name="GIGACHAT_BASE_URL", type="str", description="GigaChat API URL"),
            FieldSpec(name="GIGACHAT_AUTH_URL", type="str", description="GigaChat OAuth URL"),
            FieldSpec(
                name="GIGACHAT_ACCESS_TOKEN", type="secret", secret=True,
                description="Готовый Bearer-токен GigaChat. Если задан — OAuth (client_id+client_secret) не используется."
            ),
            FieldSpec(name="GIGACHAT_CLIENT_ID", type="secret", secret=True, description="GigaChat client_id (для OAuth, если нет токена выше)"),
            FieldSpec(name="GIGACHAT_CLIENT_SECRET", type="secret", secret=True, description="GigaChat client_secret (для OAuth)"),
            FieldSpec(name="GIGACHAT_SCOPE", type="enum", options=["GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"], description="Скоуп OAuth"),
            FieldSpec(name="GIGACHAT_MODEL_PRIMARY", type="str", description="Модель для ответов"),
            FieldSpec(name="GIGACHAT_MODEL_JUDGE", type="str", description="Модель для evals/judge"),
            FieldSpec(name="GIGACHAT_VERIFY_SSL", type="bool", description="Проверять TLS-сертификат GigaChat"),
            FieldSpec(name="GIGACHAT_CA_BUNDLE_PATH", type="str", description="Путь до корп-CA bundle (.pem)"),

            # OpenAI-compatible (vLLM, on-prem)
            FieldSpec(name="OPENAI_BASE_URL", type="str", description="OpenAI-compatible base URL"),
            FieldSpec(name="OPENAI_API_KEY", type="secret", secret=True, description="API key"),
            FieldSpec(name="OPENAI_MODEL", type="str", description="Имя модели"),
        ],
    ),
    GroupSpec(
        id="embeddings", title="Embeddings",
        fields=[
            FieldSpec(name="EMBEDDINGS_PROVIDER", type="enum", options=["local", "api", "mock"], description="Источник эмбеддингов"),
            FieldSpec(name="EMBEDDINGS_MODEL_NAME", type="str", description="Имя модели (sentence-transformers)"),
            FieldSpec(name="EMBEDDINGS_CACHE_DIR", type="str", description="Куда складывать веса модели"),
            FieldSpec(name="EMBEDDINGS_DEVICE", type="enum", options=["cpu", "cuda", "mps"], description="Где считать"),
            FieldSpec(name="EMBEDDINGS_BATCH_SIZE", type="int", description="Размер батча"),
            FieldSpec(name="EMBEDDINGS_API_URL", type="str", description="Если provider=api — URL"),
            FieldSpec(name="EMBEDDINGS_API_KEY", type="secret", secret=True, description="Если provider=api — ключ"),
        ],
    ),
    GroupSpec(
        id="retrieval", title="Поиск и retrieval",
        fields=[
            FieldSpec(name="VECTOR_BACKEND", type="enum", options=["", "sqlite_vec", "pgvector"], description="Бэкенд векторного индекса (пусто = по DB)"),
            FieldSpec(name="VECTOR_SEARCH_TOP_K", type="int", description="Сколько кандидатов брать из vector"),
            FieldSpec(name="TEXT_SEARCH_TOP_K", type="int", description="Сколько кандидатов брать из FTS"),
            FieldSpec(name="RETRIEVAL_RRF_K", type="int", description="Константа RRF"),
            FieldSpec(name="RETRIEVAL_FINAL_TOP_K", type="int", description="Сколько источников отдавать LLM"),
            FieldSpec(name="RERANKER_ENABLED", type="bool", description="Использовать ли reranker"),
            FieldSpec(name="RERANKER_TYPE", type="enum", options=["llm", "cross_encoder", "none"], description="Тип reranker'а"),
        ],
    ),
    GroupSpec(
        id="pii", title="PII / маскирование",
        fields=[
            FieldSpec(name="PII_ENABLED", type="bool", description="Маскировать ли PII при ингесте"),
            FieldSpec(name="PII_NER_ENABLED", type="bool", description="Натаска-NER поверх regex'ов"),
            FieldSpec(name="PII_STRICT_MODE", type="bool", description="Падать при подозрении на пропуск PII"),
            FieldSpec(name="PII_AUDIT_SAMPLE_RATE", type="float", description="Доля тикетов с детальным аудитом масок"),
        ],
    ),
    GroupSpec(
        id="ingest", title="Ингест",
        fields=[
            FieldSpec(name="INGEST_BATCH_SIZE", type="int", description="Размер батча на индексирование"),
            FieldSpec(name="INGEST_LLM_CONCURRENCY", type="int", description="Сколько LLM-вызовов параллельно (summary/judge)"),
            FieldSpec(name="INGEST_MAX_TICKET_AGE_DAYS", type="int", description="Окно для ингеста (отрезать слишком старые)"),
        ],
    ),
    GroupSpec(
        id="integrations", title="Интеграции — TLS / прокси",
        description="Общие настройки исходящих HTTP-вызовов.",
        fields=[
            FieldSpec(name="INTEGRATIONS_VERIFY_SSL", type="bool", description="Проверять TLS на исходящих интеграциях (LLM, webhooks, SM)"),
            FieldSpec(name="INTEGRATIONS_HTTP_TIMEOUT_SEC", type="int", description="Дефолтный таймаут httpx"),
            FieldSpec(name="INTEGRATIONS_PROXY_URL", type="str", description="HTTP(S) прокси для исходящих, если нужен"),
        ],
    ),
    GroupSpec(
        id="security", title="Безопасность",
        fields=[
            FieldSpec(name="SECURITY_RATE_LIMIT_PER_MINUTE", type="int", description="Rate-limit на пользователя+IP"),
            FieldSpec(name="SECURITY_MAX_BODY_BYTES", type="int", description="Макс размер тела запроса"),
            FieldSpec(name="SECURITY_CSRF_ENABLED", type="bool", description="Проверка X-CSRF-Token на unsafe-методах"),
            FieldSpec(name="SECURITY_ALLOWED_LLM_HOSTS", type="str", description="Whitelist хостов LLM (через запятую)"),
            FieldSpec(name="SECURITY_DB_AUDIT_ENABLED", type="bool", description="Писать audit-лог в БД"),
        ],
    ),
    GroupSpec(
        id="alerts", title="Алёрты",
        fields=[
            FieldSpec(name="ALERTS_ENABLED", type="bool", description="Включить фоновый watcher"),
            FieldSpec(name="ALERTS_WEBHOOK_URL", type="secret", secret=True, description="Webhook (Slack/Mattermost-incoming)"),
            FieldSpec(name="ALERTS_P95_LATENCY_THRESHOLD_MS", type="int", description="Порог p95 latency (мс)"),
            FieldSpec(name="ALERTS_NO_SOURCES_RATIO_THRESHOLD", type="float", description="Порог доли ответов без источников"),
            FieldSpec(name="ALERTS_ERROR_COUNT_THRESHOLD", type="int", description="Порог ошибок LLM за окно"),
            FieldSpec(name="ALERTS_CHECK_INTERVAL_SEC", type="int", description="Период проверки"),
        ],
    ),
    GroupSpec(
        id="ui", title="UI",
        fields=[
            FieldSpec(name="UI_DEFAULT_THEME", type="enum", options=["dark", "light", "auto"], description="Тема по умолчанию"),
            FieldSpec(name="UI_SHOW_DEBUG_PANEL", type="bool", description="Показывать debug-панель"),
        ],
    ),
]


# Маппинг env-имя → текущее значение (значение тащим из живых settings).
def _gather_live_values(s: Settings) -> dict[str, Any]:
    return {
        "APP_ENV": s.app_env, "APP_HOST": s.app_host, "APP_PORT": s.app_port,
        "LOG_LEVEL": s.log_level, "LOG_FORMAT": s.log_format,
        "DB_BACKEND": s.db.backend, "DB_SQLITE_PATH": str(s.db.sqlite_path),
        "DB_POSTGRES_HOST": s.db.postgres_host, "DB_POSTGRES_PORT": s.db.postgres_port,
        "DB_POSTGRES_DB": s.db.postgres_db, "DB_POSTGRES_USER": s.db.postgres_user,
        "DB_POSTGRES_PASSWORD": s.db.postgres_password.get_secret_value(),
        "LLM_PROVIDER": s.llm.provider,
        "LLM_TIMEOUT_SECONDS": s.llm.timeout_seconds,
        "LLM_MAX_RETRIES": s.llm.max_retries,
        "LLM_BUDGET_PER_USER_DAILY": s.llm.budget_per_user_daily,
        "GIGACHAT_BASE_URL": s.gigachat.base_url, "GIGACHAT_AUTH_URL": s.gigachat.auth_url,
        "GIGACHAT_ACCESS_TOKEN": s.gigachat.access_token.get_secret_value(),
        "GIGACHAT_CLIENT_ID": s.gigachat.client_id.get_secret_value(),
        "GIGACHAT_CLIENT_SECRET": s.gigachat.client_secret.get_secret_value(),
        "GIGACHAT_SCOPE": s.gigachat.scope,
        "GIGACHAT_MODEL_PRIMARY": s.gigachat.model_primary,
        "GIGACHAT_MODEL_JUDGE": s.gigachat.model_judge,
        "GIGACHAT_VERIFY_SSL": s.gigachat.verify_ssl,
        "GIGACHAT_CA_BUNDLE_PATH": str(s.gigachat.ca_bundle_path or ""),
        "OPENAI_BASE_URL": s.openai_compat.base_url,
        "OPENAI_API_KEY": s.openai_compat.api_key.get_secret_value(),
        "OPENAI_MODEL": s.openai_compat.model,
        "EMBEDDINGS_PROVIDER": s.embeddings.provider,
        "EMBEDDINGS_MODEL_NAME": s.embeddings.model_name,
        "EMBEDDINGS_CACHE_DIR": str(s.embeddings.cache_dir),
        "EMBEDDINGS_DEVICE": s.embeddings.device,
        "EMBEDDINGS_BATCH_SIZE": s.embeddings.batch_size,
        "EMBEDDINGS_API_URL": s.embeddings.api_url or "",
        "EMBEDDINGS_API_KEY": s.embeddings.api_key.get_secret_value(),
        "VECTOR_BACKEND": s.vector_store.backend,
        "VECTOR_SEARCH_TOP_K": s.vector_store.search_top_k,
        "TEXT_SEARCH_TOP_K": s.vector_store.text_search_top_k,
        "RETRIEVAL_RRF_K": s.retrieval.rrf_k,
        "RETRIEVAL_FINAL_TOP_K": s.retrieval.final_top_k,
        "RERANKER_ENABLED": s.reranker.enabled,
        "RERANKER_TYPE": s.reranker.type,
        "PII_ENABLED": s.pii.enabled, "PII_NER_ENABLED": s.pii.ner_enabled,
        "PII_STRICT_MODE": s.pii.strict_mode, "PII_AUDIT_SAMPLE_RATE": s.pii.audit_sample_rate,
        "INGEST_BATCH_SIZE": s.ingest.batch_size,
        "INGEST_LLM_CONCURRENCY": s.ingest.llm_concurrency,
        "INGEST_MAX_TICKET_AGE_DAYS": s.ingest.max_ticket_age_days,
        "INTEGRATIONS_VERIFY_SSL": s.integrations.verify_ssl,
        "INTEGRATIONS_HTTP_TIMEOUT_SEC": s.integrations.http_timeout_sec,
        "INTEGRATIONS_PROXY_URL": s.integrations.proxy_url,
        "SECURITY_RATE_LIMIT_PER_MINUTE": s.security.rate_limit_per_minute,
        "SECURITY_MAX_BODY_BYTES": s.security.max_body_bytes,
        "SECURITY_CSRF_ENABLED": s.security.csrf_enabled,
        "SECURITY_ALLOWED_LLM_HOSTS": s.security.allowed_llm_hosts,
        "SECURITY_DB_AUDIT_ENABLED": s.security.db_audit_enabled,
        "ALERTS_ENABLED": s.alerts.enabled,
        "ALERTS_WEBHOOK_URL": s.alerts.webhook_url,
        "ALERTS_P95_LATENCY_THRESHOLD_MS": s.alerts.p95_latency_threshold_ms,
        "ALERTS_NO_SOURCES_RATIO_THRESHOLD": s.alerts.no_sources_ratio_threshold,
        "ALERTS_ERROR_COUNT_THRESHOLD": s.alerts.error_count_threshold,
        "ALERTS_CHECK_INTERVAL_SEC": s.alerts.check_interval_sec,
        "UI_DEFAULT_THEME": s.ui.default_theme,
        "UI_SHOW_DEBUG_PANEL": s.ui.show_debug_panel,
    }


_ALLOWED_KEYS: set[str] = set()
for g in _GROUPS:
    for f in g.fields:
        _ALLOWED_KEYS.add(f.name)


def _coerce(spec: FieldSpec, raw: Any) -> str:
    """Привести значение к строке для .env, проверив тип."""
    if spec.type == "bool":
        if isinstance(raw, bool):
            return "true" if raw else "false"
        s = str(raw).strip().lower()
        if s in {"true", "1", "yes", "on"}: return "true"
        if s in {"false", "0", "no", "off"}: return "false"
        raise HTTPException(422, f"{spec.name}: ожидался bool")
    if spec.type == "int":
        try: return str(int(raw))
        except Exception as e: raise HTTPException(422, f"{spec.name}: ожидался int") from e
    if spec.type == "float":
        try: return str(float(raw))
        except Exception as e: raise HTTPException(422, f"{spec.name}: ожидался float") from e
    if spec.type == "enum" and spec.options is not None:
        v = str(raw)
        if v not in spec.options:
            raise HTTPException(422, f"{spec.name}: должно быть одно из {spec.options}")
        return v
    return str(raw)


def _mask_value(v: Any) -> str:
    """Спрятать содержимое секрета для GET-ответа: показываем длину и хвост."""
    s = str(v or "")
    if not s: return ""
    if len(s) <= 4: return "*" * len(s)
    return "*" * (len(s) - 4) + s[-4:]


class SettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


@router.get("")
async def get_settings(
    settings: Annotated[Settings, Depends(settings_dep)],
) -> dict[str, Any]:
    live = _gather_live_values(settings)
    groups_out: list[dict[str, Any]] = []
    for g in _GROUPS:
        fields_out = []
        for f in g.fields:
            raw = live.get(f.name)
            value_for_ui: Any = _mask_value(raw) if f.secret else raw
            fields_out.append(
                {
                    "name": f.name,
                    "type": f.type,
                    "description": f.description,
                    "options": f.options,
                    "secret": f.secret,
                    "restart_required": f.restart_required,
                    "value": value_for_ui,
                    "is_set": raw not in (None, "", False),
                }
            )
        groups_out.append({"id": g.id, "title": g.title, "description": g.description, "fields": fields_out})
    return {"env_file": str(_ENV_FILE.resolve()), "groups": groups_out}


# Парсер/писатель .env, который аккуратно сохраняет комментарии и порядок.
_ENV_LINE_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=")


def _read_env_lines() -> list[str]:
    if not _ENV_FILE.exists():
        return []
    return _ENV_FILE.read_text(encoding="utf-8").splitlines()


def _write_env_lines(lines: list[str]) -> None:
    _ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _apply_updates(updates: dict[str, str]) -> dict[str, str]:
    """Обновить .env. Возвращает {ключ: новое значение} для аудита."""
    lines = _read_env_lines()
    seen: set[str] = set()
    for i, line in enumerate(lines):
        m = _ENV_LINE_RE.match(line)
        if not m:
            continue
        key = m.group(1)
        if key in updates:
            new_val = updates[key]
            quoted = _quote_if_needed(new_val)
            lines[i] = f"{key}={quoted}"
            seen.add(key)
    for key, val in updates.items():
        if key in seen:
            continue
        lines.append(f"{key}={_quote_if_needed(val)}")
    _write_env_lines(lines)
    return updates


def _quote_if_needed(v: str) -> str:
    if v == "" or any(c in v for c in (' ', '\t', '"', "'", "#")):
        return '"' + v.replace('"', '\\"') + '"'
    return v


@router.patch("")
async def patch_settings(
    body: SettingsUpdate,
    _user_id: Annotated[str, Depends(get_user_id)],
) -> dict[str, Any]:
    if not body.values:
        raise HTTPException(422, "values must be non-empty")

    # Найдём specs для всех ключей и приведём типы
    spec_by_name: dict[str, FieldSpec] = {}
    for g in _GROUPS:
        for f in g.fields:
            spec_by_name[f.name] = f

    updates: dict[str, str] = {}
    for k, v in body.values.items():
        if k not in _ALLOWED_KEYS:
            raise HTTPException(422, f"unknown key: {k}")
        spec = spec_by_name[k]
        # Маскированное значение из UI (например, "****1234") не пишем —
        # пользователь не вводил новое.
        if spec.secret and isinstance(v, str) and v.startswith("*"):
            continue
        updates[k] = _coerce(spec, v)

    if not updates:
        return {"status": "noop", "written": {}, "restart_required": False}

    _apply_updates(updates)
    logger.info("settings.updated", keys=sorted(updates.keys()))
    return {
        "status": "ok",
        "written": {k: ("***" if spec_by_name[k].secret else updates[k]) for k in updates},
        "env_file": str(_ENV_FILE.resolve()),
        "restart_required": True,
    }
