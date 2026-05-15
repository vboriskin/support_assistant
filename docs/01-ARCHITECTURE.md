# 01. Architecture

## Высокоуровневая схема

```
┌────────────────────────────────────────────────────────────────────┐
│ Standalone HTML UI (vanilla JS, без сборки)                        │
│ Запросы к /api/* через fetch + SSE для streaming                   │
└──────────────────────────┬─────────────────────────────────────────┘
                           │ HTTP/JSON
                           ▼
┌────────────────────────────────────────────────────────────────────┐
│ FastAPI приложение (api/)                                          │
│   • /api/assistant/chat       — RAG-чат (stream)                   │
│   • /api/assistant/analyze    — анализ тикета (одним вызовом)      │
│   • /api/categorize           — автокатегоризация                  │
│   • /api/ingest/csv           — загрузка CSV-выгрузки тикетов      │
│   • /api/ingest/kb            — загрузка KB                        │
│   • /api/tickets/*            — список / поиск / детали            │
│   • /api/evals/*              — запуск eval-набора, история        │
│   • /ui                       — отдача статики (HTML, JS, CSS)     │
└────┬─────────────┬──────────────────────┬──────────────────────────┘
     │             │                      │
     ▼             ▼                      ▼
┌──────────┐ ┌─────────────────┐ ┌──────────────────────────────────┐
│ services │ │ pipelines       │ │ evals                            │
│  (бизнес-│ │  (ETL)          │ │  (runner + judges)               │
│ логика)  │ │                 │ │                                  │
└────┬─────┘ └─────────┬───────┘ └──────────────────────────────────┘
     │                 │
     ▼                 ▼
┌────────────────────────────────────────────────────────────────────┐
│ adapters (за абстракциями, переключаемые)                          │
│                                                                    │
│ ┌──────────┐ ┌────────────┐ ┌──────────────┐ ┌──────────────┐      │
│ │   LLM    │ │ Embeddings │ │ Vector Store │ │ Ticket Source│      │
│ │ GigaChat │ │ Local ST   │ │ SQLite-vec / │ │ CSV / SM API │      │
│ │ YandexGPT│ │ API client │ │   pgvector   │ │ (future)     │      │
│ │ OpenAI*  │ │            │ │              │ │              │      │
│ │ Mock     │ │            │ │              │ │              │      │
│ └──────────┘ └────────────┘ └──────────────┘ └──────────────┘      │
└────────────────────────────────────────────────────────────────────┘
```

## Слои и их ответственность

### `api/` — HTTP API
- Принимает HTTP-запросы, валидирует входные данные через Pydantic.
- Авторизация (на старте — `X-User-Id` для аудита, без проверки).
- CSRF и Rate limiting (см. `19-SECURITY.md`).
- Делегирует логику в `services/`.
- **Не знает** про реализацию LLM, БД, эмбеддингов.

### `services/` — бизнес-логика
- `assistant.py` — оркестрация RAG-цепочки: retrieve → rerank → build prompt → call LLM → format response.
- `retrieval.py` — гибридный поиск (вектор + BM25/FTS), Reciprocal Rank Fusion.
- `reranker.py` — переранжирование top-N кандидатов (LLM-as-reranker или cross-encoder).
- `categorizer.py` — классификация входящего обращения по таксономии.
- `ticket_search.py` — поиск тикетов по фильтрам для UI.

Сервисы получают зависимости через DI (FastAPI Depends). **Не зависят** от конкретных HTTP-фреймворков и БД-движков.

### `pipelines/` — ETL-пайплайны
- `ticket_ingestion/` — обработка тикетов: extract → normalize → mask_pii → classify_resolution → generate_summary → deduplicate → index.
- `kb_ingestion/` — индексация статей KB: extract → chunk → embed → index.

Пайплайны — это набор шагов с явной композицией. Каждый шаг — pure-функция или класс с одной публичной операцией. Шаги тестируются изолированно.

### `core/` — доменная логика
- Pydantic-модели предметной области (`Ticket`, `KBArticle`, `Source`, `Answer`, `EvalCase`).
- Утилиты, не зависящие ни от чего: `chunking`, `text_cleaning`, `prompts/` (текстовые шаблоны).
- PII-маскирование (`pii/`).

### `adapters/` — внешние системы
- Каждый адаптер реализует базовый интерфейс (Protocol/ABC).
- Конкретные реализации выбираются через factory + `.env`.

Список адаптеров:
- `adapters/llm/` — `LLMClient` (GigaChat, YandexGPT, OpenAI-compatible, Mock).
- `adapters/embeddings/` — `EmbeddingsClient` (local sentence-transformers, API client).
- `adapters/vector_store/` — `VectorStore` (sqlite-vec, pgvector).
- `adapters/text_search/` — `TextSearch` (SQLite FTS5, Postgres tsvector).
- `adapters/ticket_source/` — `TicketSource` (CSV, в будущем SM API).

### `evals/` — eval-инфраструктура
- `cases/` — JSON-файлы с эталонными кейсами.
- `runner.py` — прогон кейсов через retrieval + assistant.
- `judges/` — LLM-as-judge для faithfulness и helpfulness.
- `reports/` — сохранённые отчёты прогонов.

## Поток данных: ингест тикета

```
CSV-файл
   │
   ▼
[1] extract.py
   • Читает CSV (pandas)
   • Валидация обязательных полей
   • Создаёт raw Ticket
   │
   ▼
[2] normalize.py
   • Чистит HTML, удаляет цитаты, подписи
   • Объединяет тему + описание + комментарии в единый текст
   │
   ▼
[3] mask_pii.py
   • Regex для предсказуемых форматов (телефоны, email, номера заявок)
   • Natasha NER для ФИО, организаций
   • Замена на типизированные токены
   │
   ▼
[4] classify_resolution.py
   • LLM-вызов: тикет реально решён? (resolved/no_resolution/workaround/unclear)
   • Тикеты no_resolution не идут в индекс выжимок
   │
   ▼
[5] generate_summary.py
   • LLM-вызов: структурированная выжимка (симптом, причина, решение)
   • Возвращает JSON, валидация по схеме
   │
   ▼
[6] deduplicate.py
   • Эмбеддинг выжимки
   • Поиск похожих в индексе (cosine > 0.92)
   • Если найден дубль — пометка is_duplicate_of
   │
   ▼
[7] index.py
   • Вставка в БД: ticket, summary, embedding
   • Вставка в полнотекстовый индекс
```

## Поток данных: запрос к ассистенту

```
HTTP POST /api/assistant/chat
   • body: {query, ticket_context?}
   │
   ▼
[1] AssistantService.answer()
   │
   ▼
[2] retrieval.search(query, filters)
   • Эмбеддинг запроса
   • Векторный поиск top-30
   • Полнотекстовый поиск top-30
   • RRF объединение
   • Возврат top-15
   │
   ▼
[3] reranker.rerank(query, candidates)
   • LLM или cross-encoder
   • Возврат top-5-8
   │
   ▼
[4] PromptBuilder.build()
   • Системный промпт + контекст тикета + источники + few-shot + запрос
   │
   ▼
[5] LLMClient.chat_completion(prompt, stream=True)
   • Вызов GigaChat
   • SSE-стрим
   │
   ▼
[6] AnswerFormatter.parse()
   • Парсит [1], [2] цитаты, привязывает к источникам
   │
   ▼
HTTP response (SSE): chunks + final metadata
```

## Состояние и хранение

| Что | Где хранится |
|---|---|
| Метаданные тикетов | `tickets` table |
| Выжимки решений (LLM-generated) | `ticket_summaries` table |
| Статьи KB и их чанки | `kb_articles`, `kb_chunks` tables |
| Эмбеддинги | `embeddings` table (sqlite-vec) или `embeddings.vector` (pgvector) |
| Полнотекстовый индекс | SQLite FTS5 виртуальная таблица / Postgres tsvector |
| Логи LLM-вызовов | `llm_call_logs` table (с TTL) |
| История чатов с ассистентом | `conversations`, `messages` tables |
| Eval-кейсы | JSON-файлы в `evals/cases/` |
| Eval-отчёты | JSON-файлы в `evals/reports/` |
| Промпты | Текстовые файлы в `core/prompts/` |
| Конфигурация | `.env` файл + дефолты в `config/settings.py` |

## Принципы коммуникации между слоями

1. **API → Services:** через Pydantic-схемы (входные параметры) и доменные модели (возврат).
2. **Services → Adapters:** через интерфейсы (Protocol). Никаких прямых импортов конкретных реализаций.
3. **Adapters → внешние системы:** через httpx (для HTTP), SQLAlchemy (для БД).
4. **Пайплайны:** последовательная композиция шагов с явной передачей состояния. Каждый шаг идемпотентен по возможности.

## Throughput / scaling notes

MVP-нагрузка реалистична:
- Ингест: 100–500 тикетов в день. Один LLM-вызов на тикет + эмбеддинг = считается секундами.
- Retrieval: 50–200 запросов в день от 1-й линии. Латентность p95 — < 5 секунд (включая LLM).
- Eval: прогон 100 кейсов = ~5–10 минут.

Не требуется горизонтальное масштабирование, кэширование Redis, очереди задач. Background-задачи через FastAPI `BackgroundTasks` или простой `asyncio.create_task`. Если в будущем понадобится — добавляется Celery или ARQ без переписывания основной логики (адаптеры остаются).
