"""Создаёт БД и применяет миграции до ``head``.

Использование:

    python -m scripts.init_db

Для SQLite дополнительно создаёт каталог под файл БД и включает ``WAL``,
``foreign_keys=ON``. Для Postgres подразумевается, что БД уже создана и
доступна по реквизитам из ``.env`` — ответственность DBA/DevOps.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from alembic.config import Config

from alembic import command
from config.logging import configure_logging, get_logger
from config.settings import get_settings

ROOT = Path(__file__).resolve().parent.parent


def _apply_migrations() -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    # env.py подхватит URL из настроек; явно установить script_location нужно,
    # потому что Config может быть запущен из любой CWD.
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")


def main() -> int:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    log = get_logger("init_db")

    if settings.db.backend == "sqlite":
        path = settings.db.sqlite_path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.commit()
        finally:
            conn.close()
        log.info("sqlite_prepared", path=str(path))

    log.info("alembic_upgrade", backend=settings.db.backend)
    _apply_migrations()
    log.info("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
