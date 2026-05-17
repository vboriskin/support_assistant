# Support Assistant

[![tests](https://github.com/vboriskin/support_assistant/actions/workflows/tests.yml/badge.svg)](https://github.com/vboriskin/support_assistant/actions/workflows/tests.yml)

RAG-ассистент 1-й линии поддержки банковского веб-приложения для рассмотрения кредитных заявок. Снимает с операторов рутину поиска по KB и истории закрытых тикетов, помогает с категоризацией входящих и контролем качества через автоматизированные evals.

Полная спецификация — [docs/README.md](./docs/README.md). История изменений — [CHANGELOG.md](./CHANGELOG.md). Как контрибутить — [CONTRIBUTING.md](./CONTRIBUTING.md).

## Стек

- Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.0 async.
- SQLite (+ sqlite-vec / FTS5) для разработки; Postgres (+ pgvector / tsvector) для prod — переключение через `DB_BACKEND`.
- LLM: GigaChat (OAuth или готовый Bearer-токен) / OpenAI-совместимый шлюз / mock; выбор через `LLM_PROVIDER`.
- Embeddings: `sentence-transformers` (`multilingual-e5-large` по умолчанию) / api / mock.
- NER PII: Natasha (опционально).
- UI: vanilla JS + ES modules, без сборки. 19 вкладок.
- Tests: pytest + pytest-asyncio (147 тестов) + Playwright E2E.

## Быстрый старт — выбери один из 4 скриптов

```bash
git clone https://github.com/vboriskin/support_assistant.git
cd support_assistant
./run_demo.sh                  # самый простой путь — mock-LLM + 200 демо-тикетов
```

| Скрипт | Когда брать |
|---|---|
| `./run.sh` | **Боевой режим без демо-данных.** `.env` создаётся пустой — настроишь через UI «Настройки». |
| `./run_demo.sh` | **Demo с данными.** `.env.demo` (mock-LLM, mock-embeddings) + сидинг 200 тикетов. |
| `./run_fresh.sh` | **Боевой, начисто.** Снос `.venv`, БД, `.env`, токена; затем боевой. |
| `./run_demo_fresh.sh` | **Demo, начисто.** Полное обнуление + demo. |

Все четыре при первом запуске спрашивают токен корп-pypi-зеркала (если нужен) и сохраняют его в `.sber_pypi_token` с `chmod 600`. Дальше — venv, зависимости, миграции, uvicorn на `http://127.0.0.1:8000/ui`.

Подробнее — [docs/RUN-SCRIPTS.md](./docs/RUN-SCRIPTS.md).

## UI — 19 вкладок

| Раздел | Что делает |
|---|---|
| **Сводка** | KPI, timeseries тикетов по дням, аномалии-модулей, последний ингест |
| **Ассистент** | Чат с SSE-стримом, ticket-context picker, цитаты [N]↔источник, multi-turn clarify |
| **Тикеты** | Список + поиск + детальная карточка с кнопками «Анализ», «Переиндексировать» |
| **История** | Прошлые диалоги с источниками и feedback |
| **База знаний** | CRUD статей + bulk-импорт zip/md |
| **Ингест** | Drag-n-drop CSV, прогресс-бар по SSE |
| **Слабые ответы** | 👎 / no-sources / declined — с кнопкой «+ В eval-набор» |
| **Evals** | Запуск прогона, per-case карточки, сравнение двух прогонов |
| **Здоровье** | Пинги LLM / embeddings / vector_store / FTS + coverage по модулям |
| **Стоимость** | Токены и латентность по типам вызовов, p95 |
| **Промпты** | Версии системного промпта + sandbox A/B сравнение |
| **Few-shot** | Модерация эталонных пар (pending → approved → в промпт) |
| **Устаревшее KB** | Статьи, давно не обновлявшиеся и с негативным feedback |
| **PII playground** | Тест маскирования: встроенные + пользовательские regex |
| **Аудит** | Кто, когда и что менял через API |
| **Алёрты** | Текущие сигналы, пороги, кнопка «Проверить сейчас» |
| **Артефакты** | Спецификация всего, что должна собрать команда поддержки |
| **Инструкция** | Пошаговое руководство для нового пользователя |
| **Настройки** | Все настройки приложения с маскированием секретов + кнопки «Выгрузить логи» и «Обновить приложение» |
| **Описание** | Архитектура приложения с 3-х перспектив: бизнес-, системный аналитик, разработчик |

## Demo-артефакты

В `demo_artifacts/` лежат 3 варианта синтетических данных:

- **v1_credit** — кредитный конвейер (текущий контекст, ~80 тикетов, 19 KB-статей, 28 eval-кейсов)
- **v2_retail** — интернет-банк для физлиц
- **v3_corp** — корпоративный банк

Каждый комплект — 14 артефактов: тикеты, KB, playbook'и, eval-кейсы, известные баги, глоссарий, регламент эскалации, SLA-матрица, контакты владельцев модулей и т.д. Подробнее — [demo_artifacts/README.md](./demo_artifacts/README.md).

## Тесты и CI

```bash
pytest                                              # 147 unit+integration
RUN_SQLITE_VEC_TESTS=1 pytest                       # + 7 тестов sqlite_vec (нужен совместимый стенд)
ruff check .                                        # линт
python -m scripts.e2e_audit --base-url http://...   # Playwright E2E
```

В [GitHub Actions](https://github.com/vboriskin/support_assistant/actions/workflows/tests.yml) на каждый push/PR в `main` прогоняются 4 джоба:

- `pytest (3.11)`, `pytest (3.12)` — unit + integration на 2 версиях Python
- `lint` — ruff check
- `e2e (Playwright)` — Chromium headless: 20 страниц + 7 сценариев + диагностика

`main` защищён branch protection: PR не смерджится, пока все 4 чека не зелёные. Подробнее — [CONTRIBUTING.md](./CONTRIBUTING.md), [docs/E2E-AUDIT.md](./docs/E2E-AUDIT.md).

## Структура

```
config/         Settings (pydantic), логирование (structlog)
core/           Доменные модели, PII, prompts, чистка, security utils
adapters/       LLM, embeddings, vector_store, text_search, ticket_source
db/             ORM, engine, repositories, alembic (3 миграции)
pipelines/      Ingest CSV → mask → classify → summary → index
services/       RAG: retrieval, reranker, prompt_builder, assistant, categorizer
api/            FastAPI: 19 роутов + middleware (rate-limit/audit/CSRF/no-cache)
ui/             SPA: index.html + js + css, без сборки. 20 страниц
evals/          Кейсы, метрики, judges, runner
scripts/        init_db, ingest_tickets, run_evals, e2e_audit, download_models
docs/           Спецификация и runbook'и
demo_artifacts/ Синтетические данные в 3 вариантах
tests/          unit + integration + golden_pii (147 тестов)
.github/workflows/  pytest × 2 + lint + e2e
```

## Переключение на prod-LLM (GigaChat)

Получить credentials и сетевой доступ — см. [docs/GIGACHAT-ONBOARDING.md](./docs/GIGACHAT-ONBOARDING.md). Затем либо через UI «Настройки → LLM», либо в `.env`:

```
LLM_PROVIDER=gigachat
# Вариант 1: готовый Bearer-токен
GIGACHAT_ACCESS_TOKEN=eyJh...

# Вариант 2: OAuth client_credentials
GIGACHAT_CLIENT_ID=<UUID>
GIGACHAT_CLIENT_SECRET=<BASE64>
GIGACHAT_SCOPE=GIGACHAT_API_CORP

GIGACHAT_VERIFY_SSL=true
GIGACHAT_CA_BUNDLE_PATH=/etc/ssl/certs/sber-ca-bundle.pem
EMBEDDINGS_PROVIDER=local
SECURITY_CSRF_ENABLED=true
PII_STRICT_MODE=true
```

При попытке указать любой другой LLM-хост приложение упадёт на старте — см. [core/security.py](core/security.py) `assert_allowed_llm_host`.

## Security

- 15-pt checklist — [docs/SECURITY-CHECKLIST.md](./docs/SECURITY-CHECKLIST.md).
- Модель угроз и слои защит — [docs/19-SECURITY.md](./docs/19-SECURITY.md).
- PII pipeline (13 типов, golden-набор тестов) — [docs/08-PII-MASKING.md](./docs/08-PII-MASKING.md).
- Diag-выгрузка (UI «Настройки → Выгрузить логи») гарантированно не выпускает секреты в plain — только маски `****1234` и флаги `*_set: bool`.

Все error-логи проходят через `redact_secrets` (Bearer / access_token / refresh_token / api_key / JSON-поля client_secret / password / token).

## Поддерживаемые провайдеры

| Слой | Провайдеры | Переменная |
|---|---|---|
| LLM | gigachat / openai_compatible / mock (yandexgpt — стаб) | `LLM_PROVIDER` |
| Embeddings | local (sentence-transformers) / api / mock | `EMBEDDINGS_PROVIDER` |
| БД | sqlite / postgres | `DB_BACKEND` |
| Vector store | sqlite_vec / pgvector (авто по DB_BACKEND) | `VECTOR_BACKEND` |
| Text search | SQLite FTS5 / Postgres tsvector (по DB_BACKEND) | — |

## Лицензия

Proprietary. Внутренний проект банка.
