"""Интеграционные тесты FastAPI-приложения.

Каждый тест поднимает приложение в-процессе через ``httpx.AsyncClient`` +
``ASGITransport``. Зависимости (LLM, embeddings, vector_store, БД-сессия)
подменяются через ``app.dependency_overrides``. БД — SQLite в ``tmp_path``;
схема создаётся ``Base.metadata.create_all`` (без alembic).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from adapters.embeddings.mock import MockEmbeddingsClient
from adapters.llm.mock import MockLLMClient
from adapters.text_search.base import TextSearchRecord
from adapters.text_search.sqlite_fts import SQLiteFTS5
from adapters.vector_store.base import VectorRecord
from api.dependencies import (
    embeddings_client,
    get_session,
    llm_client,
    text_search_client,
    vector_store_client,
)
from api.main import create_app
from config.settings import Settings
from db.base import Base
from db.engine import _install_sqlite_hooks

from ._in_memory_vector_store import InMemoryVectorStore

pytestmark = pytest.mark.integration

DIM = 32


def _settings() -> Settings:
    s = Settings()
    object.__setattr__(s.embeddings, "dimension", DIM)
    object.__setattr__(s.reranker, "enabled", False)
    object.__setattr__(s.security, "rate_limit_per_minute", 1000)
    return s


@pytest.fixture
async def app_with_mocks(tmp_path: Path) -> AsyncIterator[tuple[Any, Any, MockLLMClient, InMemoryVectorStore]]:
    db_path = tmp_path / "api.db"
    engine: AsyncEngine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", future=True
    )
    _install_sqlite_hooks(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    settings = _settings()
    llm = MockLLMClient(
        responses={"=== Вопрос пользователя ===": "Краткий ответ [1]."},
    )
    emb = MockEmbeddingsClient(dimension=DIM)
    vec = InMemoryVectorStore()
    fts = SQLiteFTS5(settings, engine)

    sample_text = "Загрузка PDF выписки лимит 5 МБ"
    await vec.upsert(
        [
            VectorRecord(
                id="ts:T1",
                target_type="ticket_summary",
                target_id="T1",
                text=sample_text,
                metadata={"module": "Документы"},
                vector=emb._vector(sample_text),
            )
        ]
    )
    await fts.upsert(
        [
            TextSearchRecord(
                id="ts:T1",
                target_type="ticket_summary",
                target_id="T1",
                title="Загрузка PDF",
                content=sample_text,
            )
        ]
    )

    app = create_app()

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[llm_client] = lambda: llm
    app.dependency_overrides[embeddings_client] = lambda: emb
    app.dependency_overrides[vector_store_client] = lambda: vec
    app.dependency_overrides[text_search_client] = lambda: fts
    app.dependency_overrides[get_session] = _override_session

    try:
        yield app, factory, llm, vec
    finally:
        await engine.dispose()


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_health_returns_ok(app_with_mocks) -> None:
    app, *_ = app_with_mocks
    async with _client(app) as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_ready_includes_vector_store_status(app_with_mocks) -> None:
    app, *_ = app_with_mocks
    async with _client(app) as c:
        r = await c.get("/ready")
    assert r.status_code == 200
    assert r.json()["vector_store"] == "ok"


async def test_assistant_chat_returns_answer_with_citation(app_with_mocks) -> None:
    app, *_ = app_with_mocks
    async with _client(app) as c:
        r = await c.post("/api/assistant/chat", json={"query": "Как загрузить выписку PDF?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"]
    assert body["model_used"] == "mock-llm"
    assert any(cit["source_index"] == 1 for cit in body["citations"])


async def test_assistant_chat_empty_query_is_422(app_with_mocks) -> None:
    app, *_ = app_with_mocks
    async with _client(app) as c:
        r = await c.post("/api/assistant/chat", json={"query": ""})
    assert r.status_code == 422
    assert r.json()["error"] == "validation"


async def test_assistant_chat_too_long_query_is_422(app_with_mocks) -> None:
    app, *_ = app_with_mocks
    async with _client(app) as c:
        r = await c.post("/api/assistant/chat", json={"query": "a" * 4001})
    assert r.status_code == 422


async def test_categorize_endpoint(app_with_mocks) -> None:
    app, _factory, llm, _ = app_with_mocks
    import json as _json

    llm.responses["Доступные модули системы"] = _json.dumps(
        {
            "category": "Загрузка",
            "module": "Документы",
            "type": "bug",
            "urgency": "normal",
            "confidence": 0.8,
            "suggested_assignee_group": "L1_support",
            "reasoning": "ok",
        }
    )
    async with _client(app) as c:
        r = await c.post(
            "/api/categorize",
            json={"subject": "Не работает PDF", "description": "Ошибка при загрузке"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["categorization"]["module"] == "Документы"
    assert body["categorization"]["type"] == "bug"
    assert body["latency_ms"] >= 0


async def test_ingest_csv_creates_job_and_get_job_returns_it(app_with_mocks) -> None:
    app, *_ = app_with_mocks
    csv_data = (
        "external_id,created_at,status,subject,description\n"
        "SM-1,2026-04-01T10:00:00,open,Тест,описание\n"
    ).encode()
    async with _client(app) as c:
        r = await c.post(
            "/api/ingest/csv",
            files={"file": ("tickets.csv", csv_data, "text/csv")},
        )
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]

        r2 = await c.get(f"/api/ingest/jobs/{job_id}")
    assert r2.status_code == 200
    assert r2.json()["id"] == job_id


async def test_ingest_csv_empty_file_is_400(app_with_mocks) -> None:
    app, *_ = app_with_mocks
    async with _client(app) as c:
        r = await c.post(
            "/api/ingest/csv",
            files={"file": ("empty.csv", b"", "text/csv")},
        )
    assert r.status_code == 400


async def test_tickets_list_returns_pagination_shape(app_with_mocks) -> None:
    app, *_ = app_with_mocks
    async with _client(app) as c:
        r = await c.get("/api/tickets")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert body["page"] == 1


async def test_conversations_crud(app_with_mocks) -> None:
    app, *_ = app_with_mocks
    async with _client(app) as c:
        r = await c.post("/api/conversations", json={"title": "demo"})
        assert r.status_code == 200, r.text
        conv = r.json()
        r2 = await c.get("/api/conversations")
        assert r2.status_code == 200
        assert any(item["id"] == conv["id"] for item in r2.json())
        r3 = await c.get(f"/api/conversations/{conv['id']}")
        assert r3.status_code == 200
        assert r3.json()["messages"] == []
        r4 = await c.get("/api/conversations/missing-id")
        assert r4.status_code == 404


async def test_evals_run_accepts_body_and_returns_run_id(app_with_mocks) -> None:
    app, *_ = app_with_mocks
    async with _client(app) as c:
        r = await c.post(
            "/api/evals/run", json={"case_set": "no_answer", "sample_size": 1}
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "started"
    assert body["run_id"]


async def test_stats_dashboard_returns_summary(app_with_mocks) -> None:
    app, *_ = app_with_mocks
    async with _client(app) as c:
        r = await c.get("/api/stats/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert "tickets_total" in body
    assert "llm_calls_total" in body


async def test_streaming_returns_sse_chunks(app_with_mocks) -> None:
    app, *_ = app_with_mocks
    async with _client(app) as c:
        async with c.stream(
            "POST",
            "/api/assistant/chat/stream",
            json={"query": "PDF выписка"},
        ) as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers.get("content-type", "")
            text = ""
            async for line in r.aiter_lines():
                text += line + "\n"
    assert '"type":"sources"' in text or '"type": "sources"' in text
    assert "[DONE]" in text
