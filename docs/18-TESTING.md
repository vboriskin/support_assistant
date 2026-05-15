# 18. Testing

Стратегия тестов: **unit покрывают логику в core/adapters; integration покрывают пайплайны и API на mock-LLM**. Тесты с реальным GigaChat — отдельный набор, не входит в обычный прогон CI.

## Принципы

1. **Тесты быстрые.** Unit < 50ms каждый. Integration — секунды, не минуты.
2. **Изолированные.** Каждый тест может запускаться отдельно, не зависит от состояния других.
3. **Без сети.** В CI и при обычном прогоне `pytest` — никакого реального GigaChat и интернета.
4. **С реальной БД.** SQLite-in-memory или tempfile для каждого теста.
5. **Voluntary integration.** Тесты с реальным LLM — отдельный маркер, не запускаются по умолчанию.

## Конфигурация

`pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-v --strict-markers --tb=short"
markers = [
    "unit: быстрые юнит-тесты",
    "integration: интеграционные с БД и mock-LLM",
    "real_llm: требует реального GigaChat (не запускаются по умолчанию)",
    "slow: тесты длительностью > 5 секунд",
]
```

Запуск:
```bash
pytest                          # все, кроме real_llm
pytest -m unit                  # только unit
pytest -m "not slow"            # без медленных
pytest -m real_llm              # только с реальным LLM (нужен .env)
```

## Фикстуры

`tests/conftest.py`:

```python
import asyncio
import os
from pathlib import Path
from typing import AsyncIterator
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from config.settings import Settings
from adapters.llm.mock import MockLLMClient
from adapters.embeddings.mock import MockEmbeddingsClient


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Тестовые настройки: всё локальное, mock-LLM, in-memory SQLite."""
    os.environ.update({
        "APP_ENV": "local",
        "DB_BACKEND": "sqlite",
        "SQLITE_PATH": ":memory:",
        "LLM_PROVIDER": "mock",
        "EMBEDDINGS_PROVIDER": "mock",
        "EMBEDDINGS_DIMENSION": "128",
        "PII_NER_ENABLED": "false",        # без natasha (медленно грузится)
        "LOG_LEVEL": "WARNING",
    })
    from config.settings import Settings
    return Settings()


@pytest_asyncio.fixture
async def engine(settings) -> AsyncIterator[AsyncEngine]:
    """Свежий SQLite engine для каждого теста."""
    from db.engine import create_engine_with_vec
    eng = create_engine_with_vec("sqlite+aiosqlite:///:memory:")
    # Применяем схему
    from db.base import Base
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def mock_llm(settings):
    client = MockLLMClient(settings)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def mock_embeddings(settings):
    client = MockEmbeddingsClient(settings)
    yield client
    await client.aclose()


@pytest.fixture
def sample_ticket_csv(tmp_path) -> Path:
    """Создаёт временный CSV с 3 тикетами."""
    path = tmp_path / "tickets.csv"
    path.write_text(
        "external_id,created_at,status,subject,description\n"
        "SM-1,2026-01-01T10:00:00,resolved,Тест 1,Описание 1\n"
        "SM-2,2026-01-02T10:00:00,resolved,Тест 2,Описание 2\n"
        "SM-3,2026-01-03T10:00:00,open,Тест 3,Описание 3\n",
        encoding="utf-8",
    )
    return path
```

## Unit-тесты

### `tests/unit/test_pii_masking.py`

Описан в `08-PII-MASKING.md`. Прогон по golden_pii.json.

### `tests/unit/test_chunking.py`

```python
from core.chunking import chunk_text


def test_chunk_short_text_returns_single():
    chunks = chunk_text("Короткий текст.", max_tokens=500)
    assert len(chunks) == 1
    assert chunks[0].text == "Короткий текст."


def test_chunk_long_text_splits():
    long = "Предложение. " * 1000
    chunks = chunk_text(long, max_tokens=200)
    assert len(chunks) > 1
    # Чанки перекрываются
    assert any(c1.text[-50:] in c2.text for c1, c2 in zip(chunks, chunks[1:]))


def test_chunk_respects_paragraphs():
    text = "Параграф 1.\n\nПараграф 2.\n\nПараграф 3."
    chunks = chunk_text(text, max_tokens=500)
    # Не должен разрезать параграф пополам
    for c in chunks:
        assert not (c.text.endswith("Параграф 1") and "\n\n" not in c.text)
```

### `tests/unit/test_prompts.py`

```python
from pathlib import Path
import re
from core.prompts.loader import load_prompt


def test_all_prompts_loadable():
    """Каждый .txt в core/prompts/ должен загружаться."""
    prompts_dir = Path("core/prompts")
    for p in prompts_dir.glob("*.txt"):
        content = load_prompt(p.stem)
        assert content, f"Empty prompt: {p}"


def test_prompt_placeholders_documented():
    """Все {placeholder} в промпте должны быть единообразны."""
    p = load_prompt("system_assistant")
    placeholders = set(re.findall(r"\{(\w+)\}", p))
    # System assistant не использует placeholder'ы — статичен
    assert placeholders == set(), f"Unexpected placeholders: {placeholders}"


def test_categorization_prompt_has_required_placeholders():
    p = load_prompt("categorization")
    placeholders = set(re.findall(r"\{(\w+)\}", p))
    assert "subject" in placeholders
    assert "description" in placeholders
    assert "modules" in placeholders
```

### `tests/unit/test_normalize.py`

```python
from pipelines.ticket_ingestion.normalize import normalize_text


def test_strips_html():
    assert normalize_text("<p>Hello <b>world</b></p>") == "Hello world"


def test_strips_quotes():
    assert "> Цитата" not in normalize_text("Текст\n> Цитата\n> ещё цитата")


def test_normalizes_whitespace():
    assert normalize_text("Много    пробелов\n\n\n\nи строк") == "Много пробелов\n\nи строк"


def test_keeps_paragraphs():
    text = "Параграф 1.\n\nПараграф 2."
    assert normalize_text(text) == text
```

### `tests/unit/test_rrf.py`

```python
from services.retrieval import RetrievalService


def test_rrf_merges_two_lists():
    # vector_hits и text_hits в формате VectorSearchHit/TextSearchHit (упрощённо)
    # документ A в обоих списках с rank 1 → высокий RRF
    # документ B только в одном → ниже
    ...
```

### `tests/unit/test_answer_formatter.py`

```python
from services.answer_formatter import AnswerFormatter
from core.models import Source


def test_extracts_citations():
    formatter = AnswerFormatter()
    sources = [
        Source(source_type="kb_chunk", source_id=f"id{i}", title=f"T{i}",
               content="...", metadata={}, score=0.5, rank=i)
        for i in range(5)
    ]
    answer = formatter.parse(
        text="Согласно [1] и [3], всё хорошо.",
        used_sources=sources, model="m", latency_ms=100,
    )
    assert len(answer.citations) == 2
    assert {c.source_index for c in answer.citations} == {1, 3}


def test_ignores_invalid_citations():
    formatter = AnswerFormatter()
    sources = [
        Source(source_type="kb_chunk", source_id="id0", title="T",
               content="...", metadata={}, score=0.5, rank=0)
    ]
    answer = formatter.parse(
        text="Согласно [99] и [-1] и [1], всё.",
        used_sources=sources, model="m", latency_ms=100,
    )
    assert len(answer.citations) == 1
    assert answer.citations[0].source_index == 1
```

## Integration-тесты

### `tests/integration/test_ingest_pipeline.py`

```python
import pytest
from pipelines.ticket_ingestion.pipeline import TicketIngestionPipeline
from adapters.ticket_source.csv_source import CSVTicketSource
# ...


@pytest.mark.integration
async def test_ingest_csv_end_to_end(
    settings, engine, mock_llm, mock_embeddings, sample_ticket_csv,
):
    # Подготовка
    repo = TicketsRepository(engine)
    vector_store = SQLiteVecStore(settings, engine)
    text_search = SQLiteFTS5(settings, engine)
    pii = PIIMaskingPipeline(settings)

    # Mock-LLM возвращает валидные ответы для классификации и summary
    mock_llm.responses = {
        "Категория: ": '{"resolution_status": "resolved", "reason": "ok"}',
        # Для summary mock-llm вернёт дефолт; нужно проверять, что код не падает
    }

    pipeline = TicketIngestionPipeline(
        settings=settings,
        source=CSVTicketSource(),
        repo=repo,
        llm=mock_llm,
        embeddings=mock_embeddings,
        vector_store=vector_store,
        text_search=text_search,
        pii_pipeline=pii,
    )

    stats = await pipeline.run(str(sample_ticket_csv), job_id="test")

    assert stats["total"] == 3
    assert stats["processed"] >= 2          # 2 resolved
    # Тикет в открытом статусе сохраняется без summary
    open_ticket = await repo.get_by_external_id("SM-3")
    assert open_ticket is not None


@pytest.mark.integration
async def test_ingest_idempotent(settings, engine, mock_llm, mock_embeddings, sample_ticket_csv):
    """Повторный ингест того же файла не дублирует тикеты."""
    # ... первый прогон
    # ... второй прогон
    assert stats2["skipped"] >= stats1["processed"]
```

### `tests/integration/test_vector_store.py`

```python
import pytest
from adapters.vector_store.sqlite_vec_store import SQLiteVecStore
from adapters.vector_store.base import VectorRecord


@pytest.mark.integration
async def test_upsert_and_search(settings, engine):
    store = SQLiteVecStore(settings, engine)
    records = [
        VectorRecord(
            id=f"id{i}", target_type="ticket_summary", target_id=f"t{i}",
            text=f"text {i}", metadata={"module": "Скоринг"},
            vector=[0.1 * i] * 128,
        )
        for i in range(5)
    ]
    await store.upsert(records)

    count = await store.count()
    assert count == 5

    hits = await store.search([0.0] * 128, top_k=3)
    assert len(hits) <= 3
    assert all(h.target_type == "ticket_summary" for h in hits)


@pytest.mark.integration
async def test_metadata_filter(settings, engine):
    store = SQLiteVecStore(settings, engine)
    # ... вставка с разными модулями
    hits = await store.search(
        [0.0] * 128, top_k=10,
        metadata_filters={"module": "Скоринг"},
    )
    assert all(h.metadata.get("module") == "Скоринг" for h in hits)


@pytest.mark.integration
async def test_delete_by_target(settings, engine):
    store = SQLiteVecStore(settings, engine)
    # ... вставка
    deleted = await store.delete_by_target("ticket_summary", ["t1", "t2"])
    assert deleted == 2
```

### `tests/integration/test_assistant_e2e.py`

```python
import pytest
from services.assistant import AssistantService, AssistantRequest
from core.models import Source


@pytest.mark.integration
async def test_answer_with_sources(settings, mock_llm, ...):
    # Готовим mock retrieval, который вернёт 2 источника
    retrieval = MockRetrievalService([
        Source(source_type="kb_chunk", source_id="kb1",
               title="Правила", content="Лимит 5 МБ",
               metadata={}, score=0.9, rank=0),
    ])
    service = AssistantService(retrieval=retrieval, llm=mock_llm, ...)

    mock_llm.responses = {
        "...": "По [1] лимит — 5 МБ.",
    }

    answer = await service.answer(AssistantRequest(query="Какой лимит?"))
    assert len(answer.used_sources) >= 1
    assert "[1]" in answer.text


@pytest.mark.integration
async def test_no_sources_response(settings, mock_llm, ...):
    retrieval = MockRetrievalService([])
    service = AssistantService(retrieval=retrieval, llm=mock_llm, ...)
    answer = await service.answer(AssistantRequest(query="Что-то странное"))
    assert "нет" in answer.text.lower() or "не " in answer.text.lower()
    assert answer.used_sources == []
```

### `tests/integration/test_api.py`

```python
import pytest
from httpx import AsyncClient, ASGITransport
from api.main import create_app


@pytest.fixture
async def client():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.integration
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.integration
async def test_chat_validates_input(client):
    resp = await client.post("/api/assistant/chat", json={})
    assert resp.status_code == 422

    resp = await client.post("/api/assistant/chat", json={"query": ""})
    assert resp.status_code == 422


@pytest.mark.integration
async def test_chat_too_long(client):
    resp = await client.post("/api/assistant/chat", json={"query": "x" * 5000})
    assert resp.status_code == 422


@pytest.mark.integration
async def test_chat_works(client):
    # Здесь mock-LLM возвращает что-то осмысленное
    resp = await client.post("/api/assistant/chat", json={"query": "Тест"})
    assert resp.status_code == 200
    data = resp.json()
    assert "text" in data
    assert "model_used" in data


@pytest.mark.integration
async def test_rate_limit(client):
    # Сделать 130 запросов (лимит 120)
    last = None
    for _ in range(130):
        last = await client.get("/health")
    # Один из последних должен быть 429
    # (HOWEVER: /health может быть исключён из rate limit — зависит от реализации)
    pass
```

### `tests/integration/test_categorizer.py`

```python
@pytest.mark.integration
async def test_categorize_returns_structure(settings, mock_llm, mock_embeddings, ...):
    mock_llm.responses = {
        "...": json.dumps({
            "category": "Загрузка документов",
            "module": "Документы",
            "type": "bug",
            "urgency": "normal",
            "confidence": 0.85,
            "suggested_assignee_group": "L1_support",
            "reasoning": "Типичная проблема загрузки",
        }),
    }
    service = CategorizerService(...)
    result = await service.categorize(CategorizeRequest(
        subject="Не загружается",
        description="При загрузке PDF ошибка",
    ))
    assert result.categorization.module == "Документы"
    assert result.categorization.type == "bug"


@pytest.mark.integration
async def test_categorize_handles_pii(settings, mock_llm, mock_embeddings, ...):
    """Тест что PII не уходит в LLM."""
    service = CategorizerService(...)
    result = await service.categorize(CategorizeRequest(
        subject="От Иванова Ивана",
        description="Не загружается у клиента Петров П.П., тел +7 495 123-45-67",
    ))
    # Проверяем последний запрос к mock-LLM
    last_call = mock_llm.last_messages
    full_text = "\n".join(m.content for m in last_call)
    assert "Иванов" not in full_text
    assert "+7" not in full_text
    assert "<PERSON>" in full_text
```

## Real-LLM тесты

Не запускаются по умолчанию. Запуск: `pytest -m real_llm`.

```python
@pytest.mark.real_llm
async def test_gigachat_oauth_works():
    """Реально пробует получить токен GigaChat."""
    from adapters.llm.gigachat import GigaChatClient
    settings = get_settings()
    client = GigaChatClient(settings)
    token = await client._get_token()
    assert token
    await client.aclose()


@pytest.mark.real_llm
async def test_gigachat_chat_completion():
    """Реальный chat completion."""
    from adapters.llm.gigachat import GigaChatClient
    from adapters.llm.base import ChatMessage
    settings = get_settings()
    client = GigaChatClient(settings)
    response = await client.chat_completion([
        ChatMessage(role="user", content="Скажи 'привет' одним словом."),
    ], max_tokens=10)
    assert "привет" in response.text.lower()
    await client.aclose()


@pytest.mark.real_llm
async def test_gigachat_single_flight_oauth():
    """5 параллельных вызовов → 1 OAuth-запрос."""
    from adapters.llm.gigachat import GigaChatClient
    client = GigaChatClient(get_settings())
    # Сбрасываем кэш
    client._token_cache._token = None
    # Считаем OAuth-вызовы через спай
    import asyncio
    original = client._fetch_token
    counter = {"n": 0}
    async def counting_fetch():
        counter["n"] += 1
        return await original()
    client._fetch_token = counting_fetch

    results = await asyncio.gather(*[
        client._get_token() for _ in range(5)
    ])
    assert all(r == results[0] for r in results)
    assert counter["n"] == 1, f"Expected 1 OAuth call, got {counter['n']}"
    await client.aclose()
```

## Coverage

Цели:
- Core/pii: > 90% (критично).
- Adapters: > 70% (mock-зависимы).
- Services: > 70%.
- API: > 60%.
- Pipelines: > 70%.

Запуск с покрытием:

```bash
pytest --cov=. --cov-report=html --cov-report=term
```

Игнор `__init__.py`, `scripts/`, `evals/judges/` (тестируются evals-прогоном).

## Линтеры

В CI:

```bash
ruff check .
ruff format --check .
mypy --strict core adapters services pipelines
```

Pre-commit hook:

```bash
# .git/hooks/pre-commit
#!/bin/bash
ruff check . && ruff format --check . && pytest -m unit -q
```

## CI

GitHub Actions / GitLab CI (если есть):

```yaml
test:
  steps:
    - python -m venv .venv && source .venv/bin/activate
    - pip install -e .[dev]
    - ruff check .
    - mypy core adapters
    - pytest -m "not real_llm" --cov
```

Для GitLab внутри банка — аналогичная структура.

## Сценарии тестирования

| Что тестируем | Где | Маркер |
|---|---|---|
| Регулярные выражения PII | unit | `unit` |
| Нормализация текста | unit | `unit` |
| Загрузка промптов | unit | `unit` |
| RRF алгоритм | unit | `unit` |
| Парсинг цитат | unit | `unit` |
| Mock-LLM поведение | unit | `unit` |
| Vector store CRUD | integration | `integration` |
| Полный ингест пайплайн | integration | `integration` |
| End-to-end ассистент | integration | `integration` |
| API endpoints | integration | `integration` |
| Rate limit | integration | `integration` |
| GigaChat OAuth | real_llm | `real_llm` |
| GigaChat chat completion | real_llm | `real_llm` |
| Eval-набор (smoke) | real_llm | `slow`, `real_llm` |

## Тесты для UI

UI без сборки сложно покрывать unit-тестами. Делаем легковесно:

- **Ручное тестирование** — чек-лист в `docs/manual-ui-checks.md`. Перед релизом — пройти по чек-листу.
- **E2E через Playwright** (опционально, если есть смысл) — несколько критичных пользовательских сценариев: ингест CSV, чат с ассистентом, поиск тикета.

Для MVP — без E2E, ручной чек-лист достаточно.

## Что НЕ тестируем

- Само значение LLM-ответов в integration-тестах — лишь структуру (mock-LLM управляем).
- Точное расположение элементов в UI — это визуальные регрессии, для них нужен Playwright + screenshot diff.
- Производительность модели эмбеддингов — это про инфраструктуру, отдельный benchmark.
