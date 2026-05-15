"""Фабрика vector_store.

Если ``VECTOR_BACKEND`` не задан — выбираем по ``DB_BACKEND`` (postgres →
pgvector, иначе sqlite_vec).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from config.settings import Settings
from db.engine import get_engine

from .base import VectorStore


def create_vector_store(
    settings: Settings,
    engine: AsyncEngine | None = None,
) -> VectorStore:
    backend = settings.vector_store.backend or (
        "pgvector" if settings.db.backend == "postgres" else "sqlite_vec"
    )
    eng = engine or get_engine(settings)
    if backend == "sqlite_vec":
        from .sqlite_vec_store import SQLiteVecStore

        return SQLiteVecStore(settings, eng)
    if backend == "pgvector":
        from .pgvector_store import PgVectorStore

        return PgVectorStore(settings, eng)
    raise ValueError(f"Unknown vector store backend: {backend}")
