"""UI отдаётся FastAPI: index.html + статика + SPA-фолбэк."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import create_app

pytestmark = pytest.mark.integration


async def test_ui_index_served() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/ui")
    assert r.status_code == 200
    assert "<!doctype html>" in r.text.lower()
    assert "Support Assistant" in r.text


async def test_ui_static_css_served() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/ui/static/css/theme.css")
    assert r.status_code == 200
    assert "--app-bg-base" in r.text


async def test_ui_static_js_app_served() -> None:
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/ui/static/js/app.js")
    assert r.status_code == 200
    assert "router.start" in r.text


async def test_ui_spa_fallback() -> None:
    """Любой путь под /ui (кроме /ui/static/*) → отдаём index.html для SPA-роутинга."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/ui/random/path/that/does/not/exist")
    assert r.status_code == 200
    assert "<!doctype html>" in r.text.lower()


async def test_ui_static_page_chunk_served() -> None:
    """Страничные .html-чанки доступны (роутер на стороне UI их fetch'ит)."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/ui/static/pages/assistant.html")
    assert r.status_code == 200
    assert 'data-page="assistant"' in r.text
