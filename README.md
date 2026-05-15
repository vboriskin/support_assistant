# Support Assistant

RAG-ассистент 1-й линии поддержки банковского веб-приложения для рассмотрения кредитных заявок. Снимает с операторов рутину поиска по KB и истории закрытых тикетов, помогает с категоризацией входящих и контролем качества через автоматизированные evals.

Полная спецификация — в [docs/](./docs/). Стартовая точка: [docs/README.md](./docs/README.md). План реализации, по которому собран MVP — [docs/20-IMPLEMENTATION-PLAN.md](./docs/20-IMPLEMENTATION-PLAN.md).

## Стек

- Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.0 async.
- SQLite (+ sqlite-vec / FTS5) для разработки; Postgres (+ pgvector / tsvector) для prod — переключение одной переменной `DB_BACKEND`.
- LLM: GigaChat (OAuth) / OpenAI-совместимый шлюз / mock; выбор через `LLM_PROVIDER`.
- Embeddings: `sentence-transformers` (`multilingual-e5-large` по умолчанию), api или mock.
- NER PII: Natasha (опционально).
- UI: vanilla JS + ES modules, без сборки.
- Tests: pytest + pytest-asyncio. Покрытие — 147 тестов.

## Быстрый старт — один скрипт

```bash
git clone https://github.com/vboriskin/support_assistant.git
cd support_assistant
./run.sh
```

`run.sh` сам поднимет venv, поставит зависимости, накатит миграции, засеет демо-CSV (200 тикетов) и запустит uvicorn на http://127.0.0.1:8000/ui. При первом запуске интерактивно спросит про токен корп-pypi-зеркала (если нужен) и режим работы (demo с mock-LLM или боевой).

Полезные флаги:

```bash
./run.sh --fresh         # снести всё (.venv, БД, кэши, .env) и начать с нуля
./run.sh --reset         # пересоздать БД, оставить venv и .env
./run.sh --reset-deps    # переустановить зависимости
./run.sh --no-seed       # не засеивать sample_tickets.csv
./run.sh --no-prompt     # без интерактивных вопросов (CI)
./run.sh --port 8080
./run.sh --help
```

## Быстрый старт вручную (если не хочется `run.sh`)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cp .env.demo .env                   # mock-LLM, без интернета
python -m scripts.init_db           # миграции
python -m scripts.ingest_tickets data/sample_tickets.csv   # 200 демо-тикетов
uvicorn api.main:app --reload
```

## Демо-артефакты

В `demo_artifacts/` лежат 3 варианта синтетических данных (~70 файлов): v1 — кредитный конвейер, v2 — интернет-банк, v3 — корп-банк. По каждому — тикеты, KB-статьи, playbook'и, eval-кейсы, известные баги, глоссарии. Подробнее: [demo_artifacts/README.md](./demo_artifacts/README.md).

## Тесты

```bash
pytest                          # 147 unit+integration
pytest -m unit                  # только быстрые
pytest tests/integration/test_assistant_e2e.py -v   # отдельный набор
```

Скипаются по среде: sqlite-vec (без `enable_load_extension` на python.org macOS build), pgvector (без `TEST_POSTGRES_URL`), real-LLM (без `RUN_REAL_LLM=1`).

## Структура

```
config/         Settings (pydantic), логирование (structlog)
core/           Доменные модели, PII, prompts, чистка, security utils
adapters/       LLM, embeddings, vector_store, text_search, ticket_source
db/             ORM, engine, repositories, alembic
pipelines/      Ingest CSV → mask → classify → summary → index
services/       RAG: retrieval, reranker, prompt_builder, assistant, categorizer
api/            FastAPI: routes, middleware (rate-limit / audit / CSRF), errors
ui/             SPA: index.html + js + css, без сборки
evals/          Кейсы, метрики, judges, runner
scripts/        init_db, ingest_tickets, download_models, run_evals
docs/           Спецификация и runbook'и
tests/          unit + integration + golden_pii
```

## Workflow пилота

```bash
# 1. Подготовить CSV-выгрузку тикетов (контракт — docs/03-DATA-MODELS.md).
# 2. Ингест:
python -m scripts.ingest_tickets data/tickets.csv

# 3. Открыть UI → /ui#/assistant — задать вопрос оператора.
# 4. Прогнать evals для baseline:
python -m scripts.run_evals
# 5. По /ui#/evals — посмотреть aggregate (recall, faithfulness, helpfulness,
#    adversarial pass rate).
```

## Переключение на prod-LLM (GigaChat)

Получить credentials и сетевой доступ — см. [docs/GIGACHAT-ONBOARDING.md](./docs/GIGACHAT-ONBOARDING.md). Затем в `.env`:

```
LLM_PROVIDER=gigachat
GIGACHAT_CLIENT_ID=<UUID>
GIGACHAT_CLIENT_SECRET=<BASE64>
GIGACHAT_VERIFY_SSL=true
GIGACHAT_CA_BUNDLE_PATH=/etc/ssl/certs/sber-ca-bundle.pem
EMBEDDINGS_PROVIDER=local
SECURITY_CSRF_ENABLED=true
PII_STRICT_MODE=true
```

При попытке указать любой другой хост приложение упадёт на старте — см. [core/security.py](core/security.py) `assert_allowed_llm_host`.

## Security

- 15-pt checklist реализации — [docs/SECURITY-CHECKLIST.md](./docs/SECURITY-CHECKLIST.md).
- Модель угроз и слои защит — [docs/19-SECURITY.md](./docs/19-SECURITY.md).
- PII pipeline (13 типов, golden-набор тестов) — [docs/08-PII-MASKING.md](./docs/08-PII-MASKING.md).

Все error-логи проходят через `redact_secrets` (Bearer / access_token / refresh_token / api_key / JSON-поля client_secret / password / token).

## Eval baseline

[docs/EVAL-BASELINE.md](./docs/EVAL-BASELINE.md). Mock-прогон фиксирует структурную работоспособность: `adversarial_pass_rate=1.0`, `no_answer_pass_rate=1.0`, `errored_cases=0`. Реальный baseline (с GigaChat) фиксируется на втором запуске после ингеста реальных тикетов.

## Поддерживаемые провайдеры

| Слой | Провайдеры | Переменная |
|---|---|---|
| LLM | gigachat / openai_compatible / mock (yandexgpt — стаб) | `LLM_PROVIDER` |
| Embeddings | local (sentence-transformers) / api / mock | `EMBEDDINGS_PROVIDER` |
| БД | sqlite / postgres | `DB_BACKEND` |
| Vector store | sqlite_vec / pgvector (авто по DB_BACKEND) | `VECTOR_BACKEND` |
| Text search | SQLite FTS5 / Postgres tsvector (по DB_BACKEND) | — |

## Ограничения MVP

См. [docs/20-IMPLEMENTATION-PLAN.md §"Что НЕ делать сразу"](./docs/20-IMPLEMENTATION-PLAN.md). Кратко: без SSO, без Redis/Celery, без UI-виджета для Service Manager, без коннекторов к Confluence — это roadmap, не MVP.

## Лицензия

Proprietary. Внутренний проект банка.
