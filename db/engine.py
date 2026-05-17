"""Async-engine и session factory.

Для SQLite дополнительно настраиваем:

- ``PRAGMA foreign_keys=ON`` — SQLite по умолчанию не следит за FK;
- ``PRAGMA journal_mode=WAL`` — конкурентное чтение/запись;
- загружаем расширение ``sqlite-vec``, если оно установлено. Реальные
  ``vec0``-таблицы появляются на этапе 4 (vector store), но удобнее иметь
  расширение всегда подгруженным.

Для Postgres специальная настройка пока не нужна (``CREATE EXTENSION vector``
лежит в первой миграции pgvector — этап 4).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config.logging import get_logger
from config.settings import Settings, get_settings

logger = get_logger("db.engine")

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _install_sqlite_hooks(engine: AsyncEngine) -> None:
    """Навешивает PRAGMA-настройки и подгружает sqlite-vec при каждом подключении."""

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_conn: Any, _: Any) -> None:
        try:
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
        except Exception as e:
            logger.warning("sqlite.pragma_failed", error=str(e))

        try:
            import sqlite_vec  # type: ignore[import-not-found]

            dbapi_conn.enable_load_extension(True)
            sqlite_vec.load(dbapi_conn)
            dbapi_conn.enable_load_extension(False)
        except ImportError:
            logger.debug("sqlite_vec.not_installed")
        except Exception as e:
            # Например, на macOS системный python3 может быть собран без поддержки
            # load_extension — не падаем, просто фиксируем.
            logger.warning("sqlite_vec.load_failed", error=str(e))


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    """Создаёт async-engine. На каждый процесс — один engine."""
    settings = settings or get_settings()
    if settings.db.backend == "sqlite":
        # SQLite: настройки пула не нужны, aiosqlite сам управляет соединениями.
        engine = create_async_engine(settings.db.url, future=True)
        _install_sqlite_hooks(engine)
    else:
        engine = create_async_engine(
            settings.db.url,
            future=True,
            pool_size=settings.db.postgres_pool_size,
            max_overflow=settings.db.postgres_max_overflow,
            pool_pre_ping=True,
        )
    return engine


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """Возвращает кэшированный engine (создаёт при первом вызове)."""
    global _engine, _session_factory
    if _engine is None:
        _engine = create_engine(settings)
        _session_factory = async_sessionmaker(
            _engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _engine


def get_session_factory(
    settings: Settings | None = None,
) -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        get_engine(settings)
    assert _session_factory is not None
    return _session_factory


async def dispose_engine() -> None:
    """Освобождает соединения и сбрасывает кэш. Нужно в lifespan и в тестах."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
