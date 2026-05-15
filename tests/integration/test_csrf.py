"""CSRF middleware: блокирует POST без токена, пропускает с действительным."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

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
from adapters.text_search.sqlite_fts import SQLiteFTS5
from api.dependencies import (
    embeddings_client,
    get_session,
    llm_client,
    text_search_client,
    vector_store_client,
)
from api.main import create_app
from config.settings import Settings
from core.security import reset_csrf_store
from db.base import Base
from db.engine import _install_sqlite_hooks

from ._in_memory_vector_store import InMemoryVectorStore

pytestmark = pytest.mark.integration

DIM = 32


@pytest.fixture
async def app_with_csrf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Any]:  # type: ignore[name-defined]
    # Включаем CSRF только для этого теста.
    monkeypatch.setenv("SECURITY_CSRF_ENABLED", "true")
    from config.settings import reset_settings_cache

    reset_settings_cache()
    reset_csrf_store()

    db_path = tmp_path / "csrf.db"
    engine: AsyncEngine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", future=True
    )
    _install_sqlite_hooks(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    settings = Settings()
    object.__setattr__(settings.embeddings, "dimension", DIM)

    app = create_app()

    async def _session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    app.dependency_overrides[llm_client] = lambda: MockLLMClient()
    app.dependency_overrides[embeddings_client] = lambda: MockEmbeddingsClient(dimension=DIM)
    app.dependency_overrides[vector_store_client] = lambda: InMemoryVectorStore()
    app.dependency_overrides[text_search_client] = lambda: SQLiteFTS5(settings, engine)
    app.dependency_overrides[get_session] = _session

    yield app
    await engine.dispose()


from typing import Any  # noqa: E402


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_post_without_csrf_is_blocked(app_with_csrf) -> None:
    async with _client(app_with_csrf) as c:
        r = await c.post(
            "/api/conversations",
            json={"title": "demo"},
            headers={"X-User-Id": "alice"},
        )
    assert r.status_code == 403
    assert r.json()["error"] == "csrf_invalid"


async def test_get_passes_without_csrf(app_with_csrf) -> None:
    async with _client(app_with_csrf) as c:
        r = await c.get("/health")
    assert r.status_code == 200


async def test_post_with_valid_csrf_passes(app_with_csrf) -> None:
    async with _client(app_with_csrf) as c:
        # 1. Получаем токен — это GET, проходит без CSRF
        r = await c.get("/api/csrf", headers={"X-User-Id": "alice"})
        assert r.status_code == 200
        token = r.json()["token"]
        assert token

        # 2. POST с токеном — пропускается
        r2 = await c.post(
            "/api/conversations",
            json={"title": "demo"},
            headers={"X-User-Id": "alice", "X-CSRF-Token": token},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["title"] == "demo"


async def test_token_is_per_user(app_with_csrf) -> None:
    """Токен alice не годится для bob."""
    async with _client(app_with_csrf) as c:
        token_alice = (await c.get("/api/csrf", headers={"X-User-Id": "alice"})).json()["token"]
        r = await c.post(
            "/api/conversations",
            json={"title": "x"},
            headers={"X-User-Id": "bob", "X-CSRF-Token": token_alice},
        )
        assert r.status_code == 403
