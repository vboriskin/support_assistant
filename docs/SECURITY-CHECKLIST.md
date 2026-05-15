# Security Checklist — MVP

Финальный чек-лист по `docs/19-SECURITY.md`. Каждый пункт — статус «реализовано / частично / отложено» с пояснением и ссылкой на код/тест.

## Стек защит

| # | Пункт | Статус | Где живёт |
|---|---|---|---|
| 1 | `.env` не закоммичен | ✅ | `.gitignore`: `.env`, `.env.local`, `.env.prod`, исключение `!.env.example` |
| 2 | `PII_ENABLED=true` по умолчанию | ✅ | `PIISettings.enabled = True` в `config/settings.py` |
| 3 | `PII_STRICT_MODE=true` по умолчанию | ✅ | `PIISettings.strict_mode = True` |
| 4 | `golden_pii.json` в CI | ✅ | `tests/unit/test_pii_masking.py` — 7 кейсов; включая `test_strict_mode_raises_on_residual_email` |
| 5 | LLM-хосты в whitelist | ✅ | `core/security.is_allowed_llm_host` + проверка в `GigaChatClient.__init__` / `OpenAICompatibleClient.__init__`. Дополнительные хосты — через `SECURITY_ALLOWED_LLM_HOSTS` |
| 6 | CSRF middleware | ✅ | `api/middleware.CSRFMiddleware` (gated `SECURITY_CSRF_ENABLED`), `GET /api/csrf` для выдачи токена, UI `ensureCsrfToken()` |
| 7 | Rate limit | ✅ | `RateLimitMiddleware` (`SECURITY_RATE_LIMIT_PER_MINUTE`) |
| 8 | Все error-логи проходят через `redact_secrets` | ✅ | LLM-адаптеры: `gigachat.py` и `openai_compatible.py` — все error-paths; общий handler в `api/errors.py` |
| 9 | Adversarial evals — pass rate 100% | ✅ | `evals/cases/adversarial/` — 3 кейса; runner вычисляет `adversarial_pass_rate`. Текущий baseline на mock-LLM: 1.0 |
| 10 | Bearer-токены не появляются в логах | ✅ | `tests/unit/test_redact_secrets.py::test_redact_bearer_token` + 7 других regex-кейсов |
| 11 | HTTPS перед приложением | ⚠️ deployment | Документировано в `docs/17-DEPLOYMENT.md`. На стороне приложения — только uvicorn loopback |
| 12 | CORS_ALLOWED_ORIGINS — только нужные | ✅ | Конфигурируется через `.env`, default `http://localhost:8000` |
| 13 | OpenAPI docs отключены в prod | ✅ | `api/main.py`: `docs_url=None if app_env == "prod"` |
| 14 | Бэкапы шифруются | ⚠️ ops | Не входит в код; см. `docs/17-DEPLOYMENT.md` и `docs/GIGACHAT-ONBOARDING.md` |
| 15 | Ротация GigaChat-credentials | ⚠️ ops | Не входит в код; процесс описан в `docs/GIGACHAT-ONBOARDING.md` |

## Дополнительные кросс-cutting защиты

| Защита | Где |
|---|---|
| `SecretStr` для всех секретов | `config/settings.py` — `SecretStr` для всех `*_secret`/`api_key`/`password` |
| Path traversal в ingest | `core/security.safe_upload_path` — uuid-префикс, фильтр символов; используется в `api/routes/ingest.py` |
| `MAX_BODY_BYTES` для CSV | `api/routes/ingest.py` — 10 МБ default; 413 при превышении |
| Параметризованные SQL | везде в `db/repositories/*` через SQLAlchemy `text()`/`bindparam` |
| Prompt injection — guard в промпте | `core/prompts/system_assistant.txt` + `services/prompt_builder._INJECTION_WARNING` |
| `verify_csrf_token` через `secrets.compare_digest` | `core/security.py` — защита от timing-атак |
| Логирование без тел / PII | `api/middleware.AuditLogMiddleware` пишет только метод/путь/статус/latency/user |

## Тесты, покрывающие чек-лист

| Тест | Покрывает пункты |
|---|---|
| `tests/unit/test_redact_secrets.py` (8) | #8, #10 |
| `tests/unit/test_pii_masking.py` (7) | #2, #3, #4 |
| `tests/unit/test_security.py` (13) | #5, path-safety, CSRF-helpers |
| `tests/unit/test_llm_host_whitelist.py` (6) | #5 |
| `tests/integration/test_csrf.py` (4) | #6 |
| `tests/integration/test_assistant_e2e.py::test_adversarial_*` | #9 |

## Что НЕ делаем в MVP (явно)

Согласно `docs/19-SECURITY.md` §"Что НЕ делаем":

- Сквозное шифрование тикетов поверх БД-шифрования.
- Multi-tenant изоляция (один контур = одна команда).
- HSM для секретов (достаточно `.env` + права доступа).
- Audit-log с подписями.
- Авто-скан зависимостей на CVE (отдельно настраивается).

Эти пункты — в roadmap, не блокеры пилота.

## Что проверить перед каждым релизом

1. `pytest -m unit -m integration` → все зелёные.
2. `python -m scripts.run_evals` → `adversarial_pass_rate == 1.0`, `must_not_mention_violations_total == 0`.
3. `git ls-files | grep -E '\.env$|\.env\.'` → пусто (в репозитории только `.env.example`).
4. На stage-стенде: `curl -i https://<host>/health` через HTTPS возвращает 200.
5. В `.env.prod` — `LLM_PROVIDER=gigachat`, `PII_STRICT_MODE=true`, `SECURITY_CSRF_ENABLED=true`.
