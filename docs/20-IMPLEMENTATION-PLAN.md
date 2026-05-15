# 20. Implementation Plan

Пошаговый план для Claude Code. Цель — двигаться слоями снизу вверх, имея на каждом шаге работающую систему (хоть и минимальную), которую можно проверить.

## Принципы планирования

1. **Vertical slices.** Каждый этап — рабочая система, не «слой без функций».
2. **Скаффолд сначала.** Лучше пустой модуль с правильной структурой, чем «накидать в одну функцию».
3. **Mock-LLM с первого дня.** Чтобы не упереться в получение GigaChat-credentials.
4. **Тесты вместе с кодом.** Для каждого модуля — соответствующий тест-файл.
5. **Локальное состояние.** До интеграционных тестов всё должно крутиться на ноутбуке без внешних сервисов.

## Этапы

### Этап 0 — Скелет и инфраструктура (1-2 дня)

**Цель:** Структура папок, базовые зависимости, dev-окружение.

Задачи:
1. Создать `pyproject.toml` с зависимостями.
2. Структура папок согласно `02-PROJECT-STRUCTURE.md`.
3. `.env.example`, `.gitignore`.
4. `config/settings.py` со всеми группами настроек.
5. `config/logging.py` — настройка structlog.
6. Скрипт `scripts/init_db.py` для создания пустой SQLite.
7. `pytest.ini`, базовый `conftest.py`.
8. `ruff`, `mypy` в `pyproject.toml`.

**Verify:** `python -m scripts.init_db` создаёт `data/app.db`. `pytest` запускается без ошибок (нет тестов — ок).

### Этап 1 — Адаптер LLM с mock (1-2 дня)

**Цель:** LLM-абстракция, MockClient работает, GigaChat скелет.

Задачи:
1. `adapters/llm/base.py` — Protocol, ChatMessage, ChatCompletionResponse.
2. `adapters/llm/exceptions.py` — все exception-классы.
3. `adapters/llm/mock.py` — полная реализация MockLLMClient.
4. `adapters/llm/gigachat.py` — полная реализация (без реального тестирования).
5. `adapters/llm/openai_compatible.py` — для dev с локальной моделью.
6. `adapters/llm/factory.py` — `create_llm_client()`.
7. Unit-тесты: `test_mock_llm.py`, `test_redact_secrets.py`.

**Verify:**
- `MockLLMClient.chat_completion(...)` возвращает ChatCompletionResponse.
- `factory.create_llm_client(settings)` с `LLM_PROVIDER=mock` возвращает MockLLMClient.
- Тесты redact_secrets для Bearer-токена и URL-параметров проходят.

### Этап 2 — Адаптер Embeddings (1 день)

**Цель:** Локальная модель эмбеддингов работает.

Задачи:
1. `adapters/embeddings/base.py` — Protocol.
2. `adapters/embeddings/local_st.py` — LocalSentenceTransformersClient.
3. `adapters/embeddings/mock.py` — детерминистический mock.
4. `adapters/embeddings/factory.py`.
5. Скрипт `scripts/download_models.py`.
6. Unit-тесты: проверки размерности, нормализации, различия document/query.

**Verify:**
- `python -m scripts.download_models` скачивает модель.
- `MockEmbeddingsClient.embed_query("test")` возвращает list[float] длины 128.
- Real model: `LocalSentenceTransformersClient` грузится за < 30 сек, возвращает вектор размерности 1024.

### Этап 3 — БД-схема и repositories (1-2 дня)

**Цель:** Базовый CRUD для тикетов, summary, conversations.

Задачи:
1. `db/base.py` — SQLAlchemy Base, async engine.
2. `db/engine.py` — `get_engine()`, загрузка sqlite-vec в connection.
3. `db/models.py` — все ORM-модели (tickets, ticket_summaries, kb_articles, kb_chunks, conversations, messages, llm_call_logs, ingest_jobs).
4. `db/repositories/` — TicketsRepository, ConversationsRepository, LLMLogsRepository, IngestJobsRepository.
5. Alembic init + первая миграция.
6. Integration-тесты на CRUD.

**Verify:**
- `alembic upgrade head` применяется без ошибок.
- В тесте создаётся тикет, читается, обновляется, удаляется.
- TicketsRepository.exists_by_external_id работает.

### Этап 4 — Vector store + text search (1-2 дня)

**Цель:** Векторный и полнотекстовый индексы работают.

Задачи:
1. `adapters/vector_store/base.py`.
2. `adapters/vector_store/sqlite_vec_store.py` — полная реализация.
3. `adapters/vector_store/pgvector_store.py` — реализация.
4. `adapters/vector_store/factory.py`.
5. `adapters/text_search/base.py`.
6. `adapters/text_search/sqlite_fts.py`.
7. `adapters/text_search/postgres_fts.py`.
8. `adapters/text_search/factory.py`.
9. Integration-тесты: upsert + search + delete для обоих vector store.

**Verify:**
- Upsert 100 записей, поиск возвращает релевантные top-K.
- Фильтр по target_type работает.
- Фильтр по metadata работает.
- delete_by_target удаляет.

### Этап 5 — PII pipeline (1-2 дня)

**Цель:** Маскирование работает, golden-тесты зелёные.

Задачи:
1. `core/pii/types.py`.
2. `core/pii/regex_masker.py` со всеми регулярками.
3. `core/pii/ner_masker.py` — Natasha (с опциональным включением).
4. `core/pii/pipeline.py` — композиция.
5. `core/pii/ticket_masking.py` — маскирование Ticket-объекта.
6. `tests/fixtures/golden_pii.json` — 20-30 кейсов.
7. Unit-тест `test_pii_masking.py` — прогон golden-набора.

**Verify:**
- Все кейсы из golden_pii.json проходят.
- В strict mode — sanity-check отлавливает оставшиеся email/phone.
- Natasha NER (если включена) ловит ФИО.

### Этап 6 — Промпты и loader (полдня)

**Цель:** Все промпты — внешние файлы, загружаются.

Задачи:
1. `core/prompts/loader.py`.
2. Все промпт-файлы:
   - `system_assistant.txt`
   - `system_ingest.txt`
   - `ticket_resolution_classifier.txt`
   - `ticket_summary.txt`
   - `categorization.txt`
   - `reranker.txt`
   - `judge_faithfulness.txt`
   - `judge_helpfulness.txt`
3. `core/prompts/few_shot/assistant_examples.json` (минимум 2-3 примера).
4. `core/prompts/few_shot/summary_examples.json`.
5. Unit-тест `test_prompts.py`.

**Verify:** `load_prompt("system_assistant")` возвращает непустую строку. Все placeholders в коде существуют в промптах.

### Этап 7 — Ingest pipeline (3-4 дня)

**Цель:** End-to-end: CSV → индекс.

Задачи:
1. `core/text_cleaning.py`.
2. `pipelines/ticket_ingestion/extract.py`.
3. `pipelines/ticket_ingestion/normalize.py`.
4. `pipelines/ticket_ingestion/mask_pii_step.py` (обёртка).
5. `pipelines/ticket_ingestion/classify_resolution.py`.
6. `pipelines/ticket_ingestion/generate_summary.py`.
7. `pipelines/ticket_ingestion/deduplicate.py`.
8. `pipelines/ticket_ingestion/index.py`.
9. `pipelines/ticket_ingestion/pipeline.py` — композиция.
10. `adapters/ticket_source/csv_source.py`.
11. CLI `scripts/ingest_tickets.py`.
12. Integration-тест на синтетическом CSV (mock-LLM).

**Verify:**
- На синтетических 5 тикетах: 3 resolved, 1 open, 1 cancelled → пайплайн обрабатывает 3, пропускает 2.
- В БД появляются tickets + summaries + embeddings + text_search.
- Повторный прогон того же CSV — все skipped.

### Этап 8 — Retrieval + assistant (3-4 дня)

**Цель:** RAG работает, возвращает ответы с цитированием.

Задачи:
1. `services/retrieval.py` — hybrid + RRF.
2. `services/reranker.py` — LLM-as-reranker.
3. `services/prompt_builder.py`.
4. `services/answer_formatter.py`.
5. `services/assistant.py` — `answer()` и `answer_stream()`.
6. Integration-тест: query → answer с цитатами на mock-LLM.

**Verify:**
- `assistant.answer(query="...")` возвращает Answer с непустым text.
- При отсутствии источников — ответ «не знаю».
- Adversarial-кейс: источник с «игнорируй» → ассистент не выполняет.

### Этап 9 — Categorizer (1 день)

**Цель:** Автокатегоризация работает.

Задачи:
1. `services/categorizer.py`.
2. Integration-тест с mock-LLM.

**Verify:**
- `categorize(subject, description)` возвращает Categorization.
- PII не утекает в LLM-запрос.

### Этап 10 — FastAPI + endpoints (2-3 дня)

**Цель:** API работает, можно curl-ить.

Задачи:
1. `api/main.py` — create_app, lifespan, middleware.
2. `api/dependencies.py` — все DI.
3. `api/middleware.py` — RateLimit, CSRF, AuditLog.
4. `api/errors.py` — exception handlers.
5. `api/schemas.py`.
6. `api/routes/health.py`.
7. `api/routes/assistant.py` (включая streaming SSE).
8. `api/routes/categorize.py`.
9. `api/routes/ingest.py`.
10. `api/routes/tickets.py`.
11. `api/routes/conversations.py`.
12. `api/routes/evals.py`.
13. `api/routes/stats.py`.
14. Integration-тесты: каждый endpoint минимально.

**Verify:**
- `uvicorn api.main:app` запускается.
- `GET /health` → 200.
- `POST /api/assistant/chat` с валидным body → 200 с Answer.
- `POST /api/ingest/csv` принимает файл, создаёт job.
- `GET /api/ingest/jobs/{id}` показывает прогресс.

### Этап 11 — UI (3-5 дней)

**Цель:** Standalone HTML-приложение, 5 страниц работают.

Задачи:
1. `ui/css/theme.css` — light + dark + auto.
2. `ui/css/base.css`, `layout.css`, `components.css`.
3. `ui/index.html`, `app.js`, `router.js`, `api.js`.
4. `ui/js/components/`: theme-toggle, loader, toast, source-card.
5. `ui/pages/dashboard.html` + `js/pages/dashboard.js`.
6. `ui/pages/assistant.html` + `js/pages/assistant.js` (с streaming SSE).
7. `ui/pages/tickets.html` + `js/pages/tickets.js`.
8. `ui/pages/ingest.html` + `js/pages/ingest.js`.
9. `ui/pages/evals.html` + `js/pages/evals.js`.

**Verify:**
- `GET /ui` отдаёт страницу.
- Переключение тем работает (persist в localStorage).
- На assistant: можно отправить query, получить streaming-ответ, увидеть источники.
- На ingest: drag-and-drop CSV, увидеть прогресс job-а.

### Этап 12 — Evals (2-3 дня)

**Цель:** Eval-набор и runner работают.

Задачи:
1. `evals/cases/typical/` — 10-15 синтетических кейсов.
2. `evals/cases/no_answer/` — 3-5 кейсов.
3. `evals/cases/adversarial/` — 3-5 кейсов.
4. `evals/metrics.py`.
5. `evals/judges/faithfulness.py`.
6. `evals/judges/helpfulness.py`.
7. `evals/runner.py`.
8. `scripts/run_evals.py`.
9. API endpoint `POST /api/evals/run`.
10. UI на странице evals: запуск, просмотр результатов.
11. Тесты на metrics.py.

**Verify:**
- `python -m scripts.run_evals --sample 5` отрабатывает, сохраняет report в `evals/reports/`.
- `pytest -m real_llm` — full eval-прогон проходит за разумное время (< 15 мин).

### Этап 13 — Шлифовка + security (2-3 дня)

**Цель:** Готовность к пилоту.

Задачи:
1. Финальный аудит безопасности по чек-листу `19-SECURITY.md`.
2. Проверка всех error-paths — нет утечек секретов в логах.
3. Прогон полного eval-набора, фиксация baseline-метрик.
4. README: как запустить с нуля.
5. Документация по интеграции с GigaChat (что просить у безопасности банка).

**Verify:** Чек-лист безопасности — все галки. Eval-baseline зафиксирован.

### Этап 14 — Пилот (текущая работа)

**Цель:** Реальные данные, реальные пользователи.

Задачи:
1. Загрузить реальную CSV-выгрузку (300-1000 тикетов).
2. Прогнать ингест.
3. Дать ссылку на UI 2-3 операторам поддержки.
4. Собрать feedback за неделю.
5. Анализ feedback: какие промпты докрутить, какие источники добавить.

## Граф зависимостей этапов

```
[0] Скелет
 │
 ├─[1] LLM adapter ─┐
 │                  │
 ├─[2] Embeddings ──┤
 │                  │
 ├─[3] БД + repos ──┤
 │                  │
 ├─[4] Vector + FTS ┤
 │                  │
 ├─[5] PII ─────────┤
 │                  │
 └─[6] Промпты ─────┤
                    │
                    ▼
                  [7] Ingest pipeline
                    │
                    ▼
                  [8] Retrieval + assistant
                    │
              ┌─────┼─────┐
              │     │     │
              ▼     ▼     ▼
            [9]   [10]  [11]
          Categ. API    UI
              │     │     │
              └─────┼─────┘
                    ▼
                  [12] Evals
                    │
                    ▼
                  [13] Шлифовка
                    │
                    ▼
                  [14] Пилот
```

Что можно делать параллельно: 1, 2, 3, 4, 5, 6 после этапа 0. 9, 10, 11 после 8.

## Оценка времени

| Этап | Время | Кумулятив |
|---|---|---|
| 0. Скелет | 1-2 дня | 1-2 |
| 1. LLM | 1-2 | 2-4 |
| 2. Embeddings | 1 | 3-5 |
| 3. БД | 1-2 | 4-7 |
| 4. Vector + FTS | 1-2 | 5-9 |
| 5. PII | 1-2 | 6-11 |
| 6. Промпты | 0.5 | 6.5-11.5 |
| 7. Ingest pipeline | 3-4 | 9.5-15.5 |
| 8. Retrieval + assistant | 3-4 | 12.5-19.5 |
| 9. Categorizer | 1 | 13.5-20.5 |
| 10. API | 2-3 | 15.5-23.5 |
| 11. UI | 3-5 | 18.5-28.5 |
| 12. Evals | 2-3 | 20.5-31.5 |
| 13. Шлифовка | 2-3 | 22.5-34.5 |
| **MVP готов** | | **~5-7 недель** |
| 14. Пилот | 2-4 недели | |

С Claude Code многие этапы пойдут в 2-3 раза быстрее. Реалистично уложить MVP в 3-4 недели вечерами или 2 недели полного рабочего времени.

## Что НЕ делать сразу

Соблазны, на которые лучше не вестись:
- Делать всё «правильно с нуля» (миграции, тесты, CI/CD) — лучше скаффолд → работающее → дорабатывать.
- Подключать настоящий GigaChat в начале — нужно вытащить credentials через всю корп-бюрократию, что займёт неделю минимум. Mock-LLM хватает на 80% разработки.
- Делать UI первым — без бэка ему нечего показывать.
- Внедрять Redis/Celery «на будущее» — обычно не нужны.
- Писать гайды по prompt engineering до того, как набралась база eval-кейсов.
- Делать сложную ролевую модель — на старте все равны.

## Контрольные точки

После каждого этапа — короткий ритуал:

1. Запустить тесты этапа: `pytest -m unit -k <module>`.
2. Запустить smoke (если применимо): открыть UI, проверить endpoint.
3. Зафиксировать baseline-метрики (если evals прогнаны).
4. Закоммитить с осмысленным сообщением.
5. Перейти к следующему этапу.

## Что предъявить заказчику в конце MVP

- Работающая система локально или в контуре.
- Загруженные тикеты (минимум 300 штук).
- 10-20 проведённых сценариев «оператор задал вопрос — получил ответ».
- Прогон evals с метриками: Recall@5, Faithfulness, Helpfulness, Adversarial.
- Демо: 15-минутная встреча, показать главные сценарии.
- Список «что дальше»: 5-10 пунктов улучшений, увиденных в пилоте.

## После MVP

В roadmap (вне этого пакета):
- Виджет для встраивания в Service Manager.
- Реальный коннектор к SM API (вместо CSV).
- Коннекторы к Confluence/Wiki.
- Финетюн модели на собственных тикетах (если объём оправдывает).
- Авторизация SSO.
- Метрики в Grafana/банковский мониторинг.
- A/B-тестирование промптов.
- Дашборды с трендами категорий и аномалиями.

Этот пакет документации — только MVP. После пилота — пересматриваем приоритеты.
