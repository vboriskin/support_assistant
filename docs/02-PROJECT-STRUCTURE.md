# 02. Project Structure

## Дерево проекта

```
support-assistant/
├── pyproject.toml                  # зависимости, скрипты, конфиг ruff/mypy/pytest
├── uv.lock                         # (опционально) lock-файл для воспроизводимости
├── README.md                       # как запустить
├── .env.example                    # шаблон конфигурации
├── .gitignore
├── alembic.ini                     # конфиг миграций
│
├── docs/                           # этот пакет документации
│
├── alembic/                        # миграции БД
│   ├── env.py
│   └── versions/
│
├── config/
│   ├── __init__.py
│   ├── settings.py                 # pydantic-settings, чтение .env
│   └── logging.py                  # настройка structlog
│
├── core/                           # доменная логика, без внешних зависимостей
│   ├── __init__.py
│   ├── models.py                   # Ticket, KBArticle, Source, Answer, EvalCase (Pydantic)
│   ├── chunking.py                 # семантический чанкинг текста
│   ├── text_cleaning.py            # удаление HTML, цитат, подписей
│   ├── pii/
│   │   ├── __init__.py
│   │   ├── types.py                # типы PII: PERSON, PHONE, EMAIL, APP_ID и т.д.
│   │   ├── regex_masker.py         # regex-маскирование
│   │   ├── ner_masker.py           # Natasha NER
│   │   ├── pipeline.py             # композиция regex + ner
│   │   └── audit.py                # подсчёт замен, аудит-лог
│   └── prompts/                    # все промпты как текстовые файлы
│       ├── __init__.py
│       ├── loader.py               # утилита загрузки промптов
│       ├── system_assistant.txt
│       ├── ticket_summary.txt
│       ├── ticket_resolution_classifier.txt
│       ├── categorization.txt
│       ├── judge_faithfulness.txt
│       ├── judge_helpfulness.txt
│       └── few_shot/
│           ├── assistant_examples.json
│           └── summary_examples.json
│
├── db/                             # модели БД и подключение
│   ├── __init__.py
│   ├── base.py                     # Base для SQLAlchemy
│   ├── engine.py                   # создание engine, session factory
│   ├── models.py                   # SQLAlchemy ORM-модели
│   └── repositories/               # шаблон Repository для каждой модели
│       ├── __init__.py
│       ├── tickets.py
│       ├── kb.py
│       ├── conversations.py
│       └── llm_logs.py
│
├── adapters/                       # внешние системы
│   ├── __init__.py
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py                 # LLMClient Protocol + ChatMessage модели
│   │   ├── exceptions.py           # LLMError, LLMRateLimitError, LLMAuthError
│   │   ├── gigachat.py             # GigaChat-адаптер с OAuth
│   │   ├── yandexgpt.py            # YandexGPT-адаптер
│   │   ├── openai_compatible.py    # для тестов с локальной моделью или OpenRouter
│   │   ├── mock.py                 # mock для тестов и dev без сети
│   │   ├── factory.py              # выбор адаптера по LLM_PROVIDER
│   │   └── streaming.py            # SSE-парсер и форвардер
│   ├── embeddings/
│   │   ├── __init__.py
│   │   ├── base.py                 # EmbeddingsClient Protocol
│   │   ├── local_st.py             # sentence-transformers
│   │   ├── api_client.py           # эмбеддинги через HTTP API
│   │   └── factory.py
│   ├── vector_store/
│   │   ├── __init__.py
│   │   ├── base.py                 # VectorStore Protocol
│   │   ├── sqlite_vec_store.py     # SQLite + sqlite-vec
│   │   ├── pgvector_store.py       # Postgres + pgvector
│   │   └── factory.py
│   ├── text_search/
│   │   ├── __init__.py
│   │   ├── base.py                 # TextSearch Protocol
│   │   ├── sqlite_fts.py           # SQLite FTS5
│   │   ├── postgres_fts.py         # Postgres tsvector
│   │   └── factory.py
│   └── ticket_source/
│       ├── __init__.py
│       ├── base.py                 # TicketSource Protocol
│       ├── csv_source.py           # чтение CSV-выгрузки
│       └── factory.py
│
├── pipelines/                      # ETL-пайплайны
│   ├── __init__.py
│   ├── ticket_ingestion/
│   │   ├── __init__.py
│   │   ├── pipeline.py             # композиция всех шагов
│   │   ├── extract.py
│   │   ├── normalize.py
│   │   ├── mask_pii_step.py
│   │   ├── classify_resolution.py
│   │   ├── generate_summary.py
│   │   ├── deduplicate.py
│   │   └── index.py
│   └── kb_ingestion/
│       ├── __init__.py
│       ├── pipeline.py
│       ├── markdown_loader.py
│       ├── html_loader.py
│       └── index.py
│
├── services/                       # бизнес-логика
│   ├── __init__.py
│   ├── assistant.py                # RAG-оркестрация
│   ├── retrieval.py                # hybrid search + RRF
│   ├── reranker.py                 # переранжирование
│   ├── categorizer.py              # категоризация
│   ├── ticket_search.py            # фильтры для UI
│   ├── ingest_orchestrator.py      # асинхронные ингест-задачи
│   └── prompt_builder.py           # сборка промпта для assistant
│
├── api/                            # FastAPI
│   ├── __init__.py
│   ├── main.py                     # FastAPI app, lifespan, middleware
│   ├── dependencies.py             # DI: get_llm, get_vector_store и т.д.
│   ├── middleware.py               # CORS, rate-limit, audit logging
│   ├── schemas.py                  # Pydantic-схемы запросов/ответов
│   ├── errors.py                   # обработчики ошибок
│   └── routes/
│       ├── __init__.py
│       ├── health.py               # /health, /ready
│       ├── assistant.py            # /api/assistant/*
│       ├── categorize.py           # /api/categorize
│       ├── ingest.py               # /api/ingest/*
│       ├── tickets.py              # /api/tickets/*
│       ├── kb.py                   # /api/kb/*
│       ├── conversations.py        # /api/conversations/*
│       ├── evals.py                # /api/evals/*
│       └── stats.py                # /api/stats для дашборда
│
├── ui/                             # standalone HTML-приложение
│   ├── index.html                  # точка входа, layout
│   ├── pages/
│   │   ├── dashboard.html
│   │   ├── assistant.html
│   │   ├── tickets.html
│   │   ├── ingest.html
│   │   └── evals.html
│   ├── js/
│   │   ├── app.js                  # роутер, layout, темизация
│   │   ├── api.js                  # обёртка над fetch
│   │   ├── components/
│   │   │   ├── loader.js
│   │   │   ├── source-card.js
│   │   │   ├── theme-toggle.js
│   │   │   └── toast.js
│   │   └── pages/
│   │       ├── dashboard.js
│   │       ├── assistant.js
│   │       ├── tickets.js
│   │       ├── ingest.js
│   │       └── evals.js
│   └── css/
│       ├── theme.css               # дизайн-токены (CSS custom properties)
│       ├── base.css                # типографика, сброс
│       ├── layout.css              # layout, навигация
│       └── components.css          # карточки, формы, кнопки
│
├── evals/
│   ├── __init__.py
│   ├── cases/                      # эталонные кейсы (JSON)
│   │   ├── typical/
│   │   ├── no_answer/
│   │   ├── ambiguous/
│   │   └── adversarial/
│   ├── runner.py                   # прогон evals
│   ├── judges/
│   │   ├── __init__.py
│   │   ├── faithfulness.py
│   │   ├── helpfulness.py
│   │   └── retrieval.py
│   ├── metrics.py                  # расчёт метрик
│   └── reports/                    # отчёты прогонов (JSON, gitignored)
│
├── scripts/                        # CLI-утилиты
│   ├── __init__.py
│   ├── ingest_tickets.py           # CLI ингеста
│   ├── reindex.py                  # переиндексация
│   ├── run_evals.py                # запуск evals из CLI
│   ├── init_db.py                  # создание схемы
│   └── seed_demo_data.py           # тестовые данные для разработки
│
└── tests/
    ├── __init__.py
    ├── conftest.py                 # фикстуры
    ├── unit/
    │   ├── test_pii_masking.py
    │   ├── test_chunking.py
    │   ├── test_prompts.py
    │   ├── test_retrieval.py
    │   └── ...
    ├── integration/
    │   ├── test_ingest_pipeline.py
    │   ├── test_assistant_e2e.py
    │   └── ...
    └── fixtures/
        ├── sample_tickets.csv
        ├── sample_kb/
        └── golden_pii.json         # «золотой» набор для PII-тестов
```

## Принципы организации

### Каждый модуль имеет один публичный интерфейс

Если файл `services/assistant.py` экспортирует `class AssistantService`, то внешний код импортирует только этот класс. Внутренние функции — приватные (`_helper_func`).

### Адаптеры за интерфейсами

В `adapters/<type>/base.py` лежит `Protocol` или `ABC`. Конкретные реализации — отдельные файлы, в `factory.py` — функция выбора по конфигу:

```python
# adapters/llm/factory.py
def get_llm_client(settings: Settings) -> LLMClient:
    provider = settings.llm_provider
    if provider == "gigachat":
        return GigaChatClient(...)
    if provider == "yandexgpt":
        return YandexGPTClient(...)
    if provider == "mock":
        return MockLLMClient(...)
    raise ValueError(f"Unknown LLM provider: {provider}")
```

### Промпты — внешние файлы

Никаких `f"..."` в коде для системных промптов. Только в `core/prompts/*.txt`. Загрузка через `core/prompts/loader.py`.

### Промпт-параметры через Jinja2-минимум

В промпт-файле используем `{{variable}}` (без Jinja, простая подстановка через `.format()` или `string.Template`). Это просто и не тащит зависимости. Сложные циклы и условия не нужны — для большинства случаев достаточно подстановки.

### Тесты повторяют структуру

Тест `tests/unit/test_pii_masking.py` тестирует `core/pii/`. Тест `tests/integration/test_ingest_pipeline.py` тестирует весь пайплайн end-to-end на mock-LLM.

## Что НЕ должно быть в проекте

- Никаких `print()` для логирования — только `structlog`.
- Никаких bare `except:` — всегда конкретный тип исключения.
- Никаких commit'ов с реальными секретами (`.env` в `.gitignore`).
- Никаких commit'ов с реальными данными клиентов в `fixtures/` или `tests/` — только синтетика.
- Никаких глобальных синглтонов — всё через DI.
