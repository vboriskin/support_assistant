"""Фикстуры для интеграционных тестов.

Для каждого теста создаётся отдельный SQLite-файл в ``tmp_path`` и схема
создаётся напрямую через ``Base.metadata.create_all`` — это в десятки раз
быстрее, чем прогон Alembic, и даёт изоляцию между тестами. Сам Alembic
проверяется отдельным тестом ``test_alembic_upgrade.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.base import Base
from db import models  # noqa: F401  — регистрация таблиц
from db.engine import _install_sqlite_hooks


@pytest.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # expire_on_commit=False — иначе в async-сессии любой доступ к атрибуту
    # после commit потребует ленивого SELECT и упадёт MissingGreenlet. Если
    # тест мутирует БД UPDATE-statement-ом, он должен явно вызвать
    # ``await session.refresh(obj)`` или перечитать объект.
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
        await s.rollback()
    await engine.dispose()


@pytest.fixture
async def vec_engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """Engine с PRAGMA + загрузкой sqlite-vec на каждом коннекте."""
    db_path = tmp_path / "vec.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    _install_sqlite_hooks(engine)
    yield engine
    await engine.dispose()
