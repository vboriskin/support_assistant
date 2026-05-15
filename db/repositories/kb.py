"""Репозиторий KB-статей и их чанков."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import KBArticle, KBChunk


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class KBRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------------- articles ----------------

    async def create_article(
        self,
        *,
        title: str,
        body: str,
        audience: str = "internal",
        module: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        source_path: str | None = None,
        id: str | None = None,
    ) -> KBArticle:
        art = KBArticle(
            id=id or str(uuid.uuid4()),
            title=title,
            body=body,
            audience=audience,
            module=module,
            category=category,
            tags_json=tags,
            updated_at=_now(),
            source_path=source_path,
            is_deprecated=False,
        )
        self.session.add(art)
        await self.session.flush()
        return art

    async def get(self, id: str, *, with_chunks: bool = False) -> KBArticle | None:
        if with_chunks:
            stmt = (
                select(KBArticle)
                .where(KBArticle.id == id)
                .options(selectinload(KBArticle.chunks))
            )
            return (await self.session.execute(stmt)).scalar_one_or_none()
        return await self.session.get(KBArticle, id)

    async def list(self, *, module: str | None = None, limit: int = 100) -> list[KBArticle]:
        stmt = select(KBArticle).order_by(KBArticle.updated_at.desc()).limit(limit)
        if module:
            stmt = stmt.where(KBArticle.module == module)
        return list((await self.session.execute(stmt)).scalars().all())

    async def update(self, id: str, **fields: Any) -> bool:
        art = await self.session.get(KBArticle, id)
        if art is None:
            return False
        for k, v in fields.items():
            setattr(art, k, v)
        art.updated_at = _now()
        await self.session.flush()
        return True

    async def delete(self, id: str) -> bool:
        stmt = delete(KBArticle).where(KBArticle.id == id)
        result = await self.session.execute(stmt)
        return bool(result.rowcount)

    # ---------------- chunks ----------------

    async def replace_chunks(
        self,
        article_id: str,
        chunks: list[dict[str, Any]],
    ) -> list[KBChunk]:
        """Удаляет старые чанки статьи и вставляет новые. Атомарно (в текущей сессии)."""
        await self.session.execute(
            delete(KBChunk).where(KBChunk.article_id == article_id)
        )
        out: list[KBChunk] = []
        for ch in chunks:
            obj = KBChunk(
                id=str(uuid.uuid4()),
                article_id=article_id,
                text=ch["text"],
                section_title=ch.get("section_title"),
                chunk_order=ch["chunk_order"],
                metadata_json=ch.get("metadata"),
            )
            self.session.add(obj)
            out.append(obj)
        await self.session.flush()
        return out

    async def list_chunks(self, article_id: str) -> list[KBChunk]:
        stmt = (
            select(KBChunk)
            .where(KBChunk.article_id == article_id)
            .order_by(KBChunk.chunk_order)
        )
        return list((await self.session.execute(stmt)).scalars().all())
