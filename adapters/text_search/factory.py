"""Фабрика text_search-адаптера. Выбор — по ``DB_BACKEND``."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from config.settings import Settings
from db.engine import get_engine

from .base import TextSearch


def create_text_search(
    settings: Settings,
    engine: AsyncEngine | None = None,
) -> TextSearch:
    eng = engine or get_engine(settings)
    if settings.db.backend == "postgres":
        from .postgres_fts import PostgresFTS

        return PostgresFTS(settings, eng)
    from .sqlite_fts import SQLiteFTS5

    return SQLiteFTS5(settings, eng)
