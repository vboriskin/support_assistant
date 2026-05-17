"""Smoke-тест: ``alembic upgrade head`` применяется на чистой SQLite-БД и
создаёт все ожидаемые таблицы. После него ``alembic downgrade base``
откатывает схему в ноль.

Не запускаем настоящий ``scripts/init_db.py`` — он лезет в реальный путь
``data/app.db``. Здесь работаем с ``tmp_path`` через ``-x url=...``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_alembic_upgrade_and_downgrade_on_sqlite(tmp_path: Path) -> None:
    db = tmp_path / "alembic.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")

    command.upgrade(cfg, "head")

    expected = {
        "tickets",
        "ticket_summaries",
        "kb_articles",
        "kb_chunks",
        "conversations",
        "messages",
        "llm_call_logs",
        "ingest_jobs",
        "alembic_version",
    }
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        conn.close()
    tables = {r[0] for r in rows}
    missing = expected - tables
    assert not missing, f"таблицы не созданы: {missing}; есть: {tables}"

    command.downgrade(cfg, "base")
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        conn.close()
    tables_after = {r[0] for r in rows}
    # alembic_version остаётся всегда; пользовательских таблиц быть не должно
    assert tables_after == {"alembic_version"}, f"осталось лишнее: {tables_after}"
