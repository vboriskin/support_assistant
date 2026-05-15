"""index tables: embeddings_meta, text_search (sqlite_vec_store, fts5).

Виртуальные ``vec0`` / ``fts5`` таблицы создаются только при наличии расширения
в рантайме. На SQLite — `_ensure_schema()` адаптеров досоздаёт их лениво.
На Postgres ``CREATE EXTENSION vector`` нужно делать с правами суперюзера
(обычно DBA), здесь — пытаемся, но не валим миграцию.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    if _is_sqlite():
        # Метаданные для sqlite-vec (саму vec0-таблицу адаптер создаёт через
        # _ensure_schema — она требует загруженного расширения).
        op.create_table(
            "embeddings_meta",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("target_type", sa.String(), nullable=False),
            sa.Column("target_id", sa.String(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=True),
        )
        op.create_index(
            "idx_emb_meta_target", "embeddings_meta", ["target_type", "target_id"]
        )
        # FTS5 — встроен в SQLite, создаём явно.
        op.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS text_search USING fts5("
            " id UNINDEXED, target_type UNINDEXED, target_id UNINDEXED,"
            " title, content,"
            " tokenize='unicode61 remove_diacritics 1')"
        )
    else:
        # Postgres: пробуем создать расширение и таблицы.
        try:
            op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception:  # noqa: BLE001
            # Без прав суперюзера — пропускаем, адаптер сам создаст в рантайме.
            pass
        try:
            op.execute(
                "CREATE TABLE IF NOT EXISTS embeddings ("
                " id TEXT PRIMARY KEY,"
                " target_type TEXT NOT NULL,"
                " target_id TEXT NOT NULL,"
                " text TEXT NOT NULL,"
                " metadata_json JSONB,"
                " vector vector(1024) NOT NULL,"
                " created_at TIMESTAMP DEFAULT NOW())"
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS idx_embeddings_target "
                "ON embeddings(target_type, target_id)"
            )
        except Exception:  # noqa: BLE001
            pass
        op.execute(
            "CREATE TABLE IF NOT EXISTS text_search ("
            " id TEXT PRIMARY KEY,"
            " target_type TEXT NOT NULL,"
            " target_id TEXT NOT NULL,"
            " title TEXT NOT NULL,"
            " content TEXT NOT NULL,"
            " tsv tsvector GENERATED ALWAYS AS ("
            "   setweight(to_tsvector('russian', coalesce(title,'')),'A') ||"
            "   setweight(to_tsvector('russian', coalesce(content,'')),'B')"
            " ) STORED)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_text_search_tsv "
            "ON text_search USING gin(tsv)"
        )


def downgrade() -> None:
    if _is_sqlite():
        op.execute("DROP TABLE IF EXISTS text_search")
        op.drop_index("idx_emb_meta_target", table_name="embeddings_meta")
        op.drop_table("embeddings_meta")
        # vec_embeddings — оставляем, она создаётся адаптером.
    else:
        op.execute("DROP TABLE IF EXISTS text_search")
        op.execute("DROP TABLE IF EXISTS embeddings")
