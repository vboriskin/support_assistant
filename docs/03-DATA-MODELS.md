# 03. Data Models

## Доменные модели (Pydantic)

Эти модели — основа домена. Используются в API-ответах, между сервисами, в пайплайнах. Лежат в `core/models.py`.

### `Ticket`

```python
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

class Ticket(BaseModel):
    """Тикет — заявка/обращение из Service Manager."""
    id: str                              # внутренний UUID
    external_id: str                     # ID в Service Manager
    channel: Literal["email", "messenger", "chatbot", "sm", "phone", "other"]
    category: str | None = None          # как в SM, если было
    module: str | None = None            # модуль приложения
    subject: str                         # тема обращения
    description: str                     # тело
    conversation: list["TicketComment"] = []  # переписка
    author_role: str | None = None       # роль автора (без ФИО)
    assignee: str | None = None          # исполнитель (без ФИО)
    status: Literal["open", "in_progress", "resolved", "closed", "cancelled"]
    priority: Literal["low", "normal", "high", "critical"] | None = None
    tags: list[str] = []
    created_at: datetime
    closed_at: datetime | None = None
    raw_fields: dict = {}                # сохранённый сырой набор полей CSV

class TicketComment(BaseModel):
    """Один комментарий в переписке."""
    author_role: str | None
    content: str
    created_at: datetime
    is_internal: bool = False            # внутренний комментарий
```

### `TicketSummary`

```python
class TicketSummary(BaseModel):
    """Результат LLM-выжимки решённого тикета."""
    ticket_id: str
    summary_one_line: str                # одно предложение
    symptom: str                         # что было сломано
    root_cause: str | None               # почему (если ясно)
    solution_steps: list[str]            # шаги решения
    affected_module: str | None
    user_role: str | None                # обобщённая роль (не ФИО)
    is_known_issue: bool                 # подходит как KB-кандидат
    resolution_status: Literal["resolved", "no_resolution", "workaround", "unclear"]
    is_duplicate_of: str | None = None   # ID канонического тикета
    generated_at: datetime
    model_used: str                      # GigaChat-Max и т.п.
```

### `KBArticle`

```python
class KBArticle(BaseModel):
    """Статья базы знаний."""
    id: str
    title: str
    body: str                            # markdown или текст
    audience: Literal["internal", "external"]  # для саппорта / для пользователей
    module: str | None
    category: str | None
    tags: list[str] = []
    updated_at: datetime
    source_path: str | None              # путь к файлу источника
    is_deprecated: bool = False

class KBChunk(BaseModel):
    """Чанк KB-статьи для индексации."""
    id: str
    article_id: str
    text: str
    section_title: str | None            # заголовок раздела внутри статьи
    chunk_order: int                     # порядок в статье
    metadata: dict = {}
```

### `Source`

```python
SourceType = Literal[
    "kb_article",
    "kb_chunk",
    "ticket_summary",
    "ticket_full",
    "playbook",
]

class Source(BaseModel):
    """Источник, найденный retriever-ом и попадающий в промпт."""
    source_type: SourceType
    source_id: str                       # ID объекта (article_id, ticket_id, ...)
    title: str                           # человекочитаемое название
    content: str                         # текст, идущий в промпт
    metadata: dict = {}                  # модуль, дата, теги
    score: float                         # релевантность от retriever
    rank: int                            # позиция после reranker (0-based)
```

### `Answer`

```python
class Citation(BaseModel):
    """Ссылка в ответе ассистента на источник."""
    source_index: int                    # [1], [2], ...
    source: Source

class Answer(BaseModel):
    """Ответ RAG-ассистента."""
    text: str                            # сам текст ответа с маркерами [1], [2]
    citations: list[Citation]
    used_sources: list[Source]
    model_used: str
    latency_ms: int
    token_usage: dict | None = None      # {prompt, completion, total}
    conversation_id: str | None = None
```

### `Categorization`

```python
class Categorization(BaseModel):
    """Результат автокатегоризации входящего."""
    category: str
    module: str | None
    type: Literal["bug", "question", "access_request", "feature_request", "incident", "duplicate", "other"]
    urgency: Literal["low", "normal", "high", "critical"]
    confidence: float                    # 0.0 — 1.0
    suggested_assignee_group: str | None
    extracted_application_id: str | None # обнаруженный ID заявки (если был)
    reasoning: str                       # короткое объяснение от модели
```

### `EvalCase`

```python
class EvalCase(BaseModel):
    """Эталонный кейс для прогона evals."""
    case_id: str
    category: str
    query: str
    ticket_context: dict | None = None   # опциональный контекст тикета
    expected_sources: list[str]          # ID источников, которые retriever должен найти
    must_mention: list[str]              # ключевые слова в ответе
    must_not_mention: list[str]
    expected_answer_summary: str         # эталон для LLM-judge
    edge_case_type: Literal["typical", "no_answer_in_kb", "ambiguous", "adversarial"]
```

## SQLAlchemy ORM-модели

Лежат в `db/models.py`. Здесь — суть таблиц. Полные ORM-классы со связями реализует Claude Code.

### `tickets`

```sql
CREATE TABLE tickets (
    id TEXT PRIMARY KEY,                       -- UUID
    external_id TEXT NOT NULL UNIQUE,          -- ID в SM
    channel TEXT NOT NULL,
    category TEXT,
    module TEXT,
    subject TEXT NOT NULL,
    description TEXT NOT NULL,
    conversation_json TEXT NOT NULL,           -- JSON-список комментариев (после маскирования)
    author_role TEXT,
    assignee TEXT,
    status TEXT NOT NULL,
    priority TEXT,
    tags_json TEXT,
    created_at TIMESTAMP NOT NULL,
    closed_at TIMESTAMP,
    raw_fields_json TEXT,                      -- сырые CSV-поля
    is_pii_masked BOOLEAN NOT NULL DEFAULT FALSE,
    masked_at TIMESTAMP,
    pii_audit_json TEXT,                       -- {PHONE: 3, EMAIL: 1, ...}
    indexed_at TIMESTAMP                       -- когда попал в индекс
);
CREATE INDEX idx_tickets_external_id ON tickets(external_id);
CREATE INDEX idx_tickets_category ON tickets(category);
CREATE INDEX idx_tickets_module ON tickets(module);
CREATE INDEX idx_tickets_created_at ON tickets(created_at);
```

### `ticket_summaries`

```sql
CREATE TABLE ticket_summaries (
    id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL UNIQUE REFERENCES tickets(id) ON DELETE CASCADE,
    summary_one_line TEXT NOT NULL,
    symptom TEXT NOT NULL,
    root_cause TEXT,
    solution_steps_json TEXT NOT NULL,         -- JSON list[str]
    affected_module TEXT,
    user_role TEXT,
    is_known_issue BOOLEAN NOT NULL,
    resolution_status TEXT NOT NULL,
    is_duplicate_of TEXT REFERENCES tickets(id),
    generated_at TIMESTAMP NOT NULL,
    model_used TEXT NOT NULL
);
CREATE INDEX idx_summaries_module ON ticket_summaries(affected_module);
CREATE INDEX idx_summaries_resolution ON ticket_summaries(resolution_status);
```

### `kb_articles` и `kb_chunks`

```sql
CREATE TABLE kb_articles (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    audience TEXT NOT NULL,
    module TEXT,
    category TEXT,
    tags_json TEXT,
    updated_at TIMESTAMP NOT NULL,
    source_path TEXT,
    is_deprecated BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE kb_chunks (
    id TEXT PRIMARY KEY,
    article_id TEXT NOT NULL REFERENCES kb_articles(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    section_title TEXT,
    chunk_order INTEGER NOT NULL,
    metadata_json TEXT
);
CREATE INDEX idx_kb_chunks_article ON kb_chunks(article_id);
```

### `embeddings` — единая таблица для всех векторов

Поле `target_type` указывает, к какой сущности относится: `kb_chunk`, `ticket_summary`, `ticket_symptom` и т.д.

**Вариант SQLite + sqlite-vec:**

```sql
CREATE TABLE embeddings (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,                 -- kb_chunk | ticket_summary | ticket_symptom
    target_id TEXT NOT NULL,
    text TEXT NOT NULL,                        -- что эмбеддили (для отладки)
    metadata_json TEXT,
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX idx_embeddings_target ON embeddings(target_type, target_id);

-- vec0 — виртуальная таблица sqlite-vec
CREATE VIRTUAL TABLE vec_embeddings USING vec0(
    embedding_id TEXT PRIMARY KEY,
    vector float[1024]
);
```

**Вариант Postgres + pgvector:**

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE embeddings (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    text TEXT NOT NULL,
    metadata_json JSONB,
    vector vector(1024) NOT NULL,
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX idx_embeddings_target ON embeddings(target_type, target_id);
CREATE INDEX idx_embeddings_vector ON embeddings USING ivfflat (vector vector_cosine_ops);
```

### Полнотекстовый индекс

**SQLite FTS5:**

```sql
CREATE VIRTUAL TABLE text_search USING fts5(
    target_type UNINDEXED,
    target_id UNINDEXED,
    title,
    content,
    tokenize = 'unicode61 remove_diacritics 1'
);
```

**Postgres tsvector:**

```sql
CREATE TABLE text_search (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('russian', coalesce(title,'')), 'A') ||
        setweight(to_tsvector('russian', coalesce(content,'')), 'B')
    ) STORED
);
CREATE INDEX idx_text_search_tsv ON text_search USING gin(tsv);
```

### `conversations` и `messages` — история чатов

```sql
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    ticket_id TEXT REFERENCES tickets(id),    -- если чат привязан к тикету
    title TEXT,                                -- сгенерированное название
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,                        -- user | assistant
    content TEXT NOT NULL,
    citations_json TEXT,                       -- список Citation
    used_sources_json TEXT,
    feedback INTEGER,                          -- -1, 0, 1 (👎, none, 👍)
    feedback_comment TEXT,
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX idx_messages_conversation ON messages(conversation_id);
```

### `llm_call_logs` — аудит LLM-вызовов

```sql
CREATE TABLE llm_call_logs (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    purpose TEXT NOT NULL,                     -- summary | answer | categorize | judge
    model TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,                 -- SHA256 промпта
    prompt_preview TEXT,                       -- первые 500 символов
    response_preview TEXT,                     -- первые 500 символов
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    latency_ms INTEGER NOT NULL,
    error TEXT,                                -- если упало
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX idx_llm_logs_purpose ON llm_call_logs(purpose);
CREATE INDEX idx_llm_logs_created ON llm_call_logs(created_at);
```

### `ingest_jobs` — отслеживание ингест-задач

```sql
CREATE TABLE ingest_jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,                    -- tickets_csv | kb
    status TEXT NOT NULL,                      -- pending | running | succeeded | failed | cancelled
    total_items INTEGER,
    processed_items INTEGER NOT NULL DEFAULT 0,
    failed_items INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    metadata_json TEXT
);
```

## Канонический формат CSV-выгрузки тикетов

Пользователь приложения сам смаппит свою выгрузку из Service Manager в этот формат. Это «контракт» с источником данных. Лежит в `docs/csv-spec.md` или в README.

### Обязательные колонки

| Колонка | Тип | Описание |
|---|---|---|
| `external_id` | string | Уникальный ID в SM |
| `created_at` | ISO datetime | Дата создания тикета (`2026-01-15T10:30:00`) |
| `status` | enum | `open` / `in_progress` / `resolved` / `closed` / `cancelled` |
| `subject` | string | Тема обращения |
| `description` | string | Тело обращения (первое сообщение) |

### Желательные колонки

| Колонка | Тип | Описание |
|---|---|---|
| `closed_at` | ISO datetime | Дата закрытия |
| `channel` | enum | `email` / `messenger` / `chatbot` / `sm` / `phone` / `other` |
| `category` | string | Категория из SM |
| `module` | string | Модуль приложения |
| `priority` | enum | `low` / `normal` / `high` / `critical` |
| `author_role` | string | Роль автора (без ФИО) |
| `assignee` | string | Исполнитель (без ФИО) |
| `tags` | string | Теги через запятую |
| `conversation` | string | JSON-строка со списком комментариев, формат ниже |

### Формат поля `conversation` (если есть)

```json
[
  {
    "author_role": "user",
    "content": "Не могу загрузить выписку, пишет ошибка",
    "created_at": "2026-01-15T10:30:00",
    "is_internal": false
  },
  {
    "author_role": "support_l1",
    "content": "Уточните, пожалуйста, размер файла",
    "created_at": "2026-01-15T11:00:00",
    "is_internal": false
  }
]
```

Если `conversation` нет — система использует `description` как единственное содержимое.

### Пример CSV (header + одна строка)

```csv
external_id,created_at,status,subject,description,closed_at,channel,category,module,priority,author_role,assignee,tags,conversation
SM-12345,2026-01-15T10:30:00,resolved,Не загружается выписка,Здравствуйте, при попытке загрузить PDF получаю ошибку,2026-01-15T14:00:00,sm,Документы,Скоринг,normal,underwriter,support_l1,"загрузка,выписка","[{""author_role"":""user"",""content"":""Здравствуйте..."",""created_at"":""2026-01-15T10:30:00""},{""author_role"":""support_l1"",""content"":""Проверьте размер файла"",""created_at"":""2026-01-15T11:00:00""}]"
```

### Что НЕ должно быть в CSV

- ФИО клиентов в полях. Если попали — будут замаскированы на этапе обработки.
- Номера паспортов, СНИЛС, банковских карт. Маскируются.
- Номера заявок клиентов в `subject`/`description` — маскируются, но рекомендуется выгрузить отдельной колонкой `extracted_application_id` для аналитики.

### Размеры

- Один CSV — до 100 000 строк (для разовой загрузки). Большие — батчами.
- Ширина одной ячейки `description` — до 50 КБ.
- Кодировка — UTF-8 без BOM или с BOM (определяется автоматически).
- Разделитель — `,`. Поля с переводами строки и запятыми — в двойных кавычках, по RFC 4180.

## Версионирование схем

При изменении доменных моделей — миграция через Alembic. Файлы миграций — в `alembic/versions/`. Имена — описательные:

```
0001_initial_schema.py
0002_add_pii_audit_to_tickets.py
0003_add_feedback_to_messages.py
```
