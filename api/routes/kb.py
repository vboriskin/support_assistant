"""KB endpoints: list / get / create / update / delete / upload / reindex.

Upload — это синхронный аплоад markdown-файла как одной статьи. Чанкование
и индексация — на ту же сессию, поэтому быстро падает на ошибках.

Полный «batch ingest» директории через CLI (`scripts/ingest_kb.py`) — roadmap;
здесь только per-article endpoints.
"""

from __future__ import annotations

from typing import Annotated, Any

import io
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from api.dependencies import (
    SessionDep,
    _session_factory,
    embeddings_client,
    get_user_id,
    text_search_client,
    vector_store_client,
)
from core.chunking import chunk_text
from db.repositories.kb import KBRepository
from pipelines.kb_ingestion.index import delete_article_index, index_article
from pipelines.kb_ingestion.pipeline import KBIngestionPipeline

router = APIRouter(prefix="/kb", tags=["kb"])


class KBArticleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    body: str = Field(..., min_length=1)
    audience: str = "internal"
    module: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    source_path: str | None = None


class KBArticleUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = None
    audience: str | None = None
    module: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    is_deprecated: bool | None = None


def _serialize(a) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "id": a.id,
        "title": a.title,
        "audience": a.audience,
        "module": a.module,
        "category": a.category,
        "tags": a.tags_json or [],
        "updated_at": a.updated_at.isoformat(),
        "source_path": a.source_path,
        "is_deprecated": a.is_deprecated,
    }


@router.get("")
async def list_articles(
    session: SessionDep,
    module: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    repo = KBRepository(session)
    items = await repo.list(module=module, limit=limit)
    return [_serialize(a) for a in items]


@router.get("/{article_id}")
async def get_article(article_id: str, session: SessionDep) -> dict[str, Any]:
    repo = KBRepository(session)
    art = await repo.get(article_id, with_chunks=True)
    if art is None:
        raise HTTPException(404, "article not found")
    out = _serialize(art)
    out["body"] = art.body
    out["chunks"] = [
        {
            "id": c.id,
            "section_title": c.section_title,
            "chunk_order": c.chunk_order,
            "text": c.text,
        }
        for c in art.chunks
    ]
    return out


@router.post("")
async def create_article(
    body: KBArticleCreate,
    session: SessionDep,
    _user_id: Annotated[str, Depends(get_user_id)],
    embeddings=Depends(embeddings_client),
    vector_store=Depends(vector_store_client),
    text_search=Depends(text_search_client),
) -> dict[str, Any]:
    # Используем общий index_article — он создаст статью + чанки + индексы.
    # Но index_article работает с session_factory; здесь — на текущей сессии.
    # Поэтому отдельная упрощённая ветка: create в БД, чанки в БД, индексы
    # после commit'а на ту же session_factory.
    repo = KBRepository(session)
    art = await repo.create_article(
        title=body.title,
        body=body.body,
        audience=body.audience,
        module=body.module,
        category=body.category,
        tags=body.tags or None,
        source_path=body.source_path,
    )
    chunks = chunk_text(body.body)
    await repo.replace_chunks(
        art.id,
        [
            {
                "text": ch.text,
                "section_title": ch.section_title,
                "chunk_order": ch.chunk_order,
            }
            for ch in chunks
        ],
    )
    await session.commit()

    # После commit'а — индексация
    if chunks:
        try:
            from adapters.vector_store.base import VectorRecord
            from adapters.text_search.base import TextSearchRecord

            texts = [c.text for c in chunks]
            vectors = await embeddings.embed_documents(texts)
            await vector_store.upsert(
                [
                    VectorRecord(
                        id=f"kb:{art.id}:{c.chunk_order}",
                        target_type="kb_chunk",
                        target_id=f"{art.id}:{c.chunk_order}",
                        text=c.text,
                        metadata={
                            "article_id": art.id,
                            "article_title": art.title,
                            "section_title": c.section_title or "",
                            "module": art.module or "",
                        },
                        vector=v,
                    )
                    for c, v in zip(chunks, vectors, strict=True)
                ]
            )
            await text_search.upsert(
                [
                    TextSearchRecord(
                        id=f"kb:{art.id}:{c.chunk_order}",
                        target_type="kb_chunk",
                        target_id=f"{art.id}:{c.chunk_order}",
                        title=(art.title + (" — " + c.section_title if c.section_title else ""))[:200],
                        content=c.text,
                    )
                    for c in chunks
                ]
            )
        except Exception:  # noqa: BLE001 — индекс упал, статья в БД есть
            pass

    return _serialize(art)


@router.put("/{article_id}")
async def update_article(
    article_id: str,
    body: KBArticleUpdate,
    session: SessionDep,
    embeddings=Depends(embeddings_client),
    vector_store=Depends(vector_store_client),
    text_search=Depends(text_search_client),
) -> dict[str, Any]:
    repo = KBRepository(session)
    payload = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if "tags" in payload:
        payload["tags_json"] = payload.pop("tags")
    ok = await repo.update(article_id, **payload)
    if not ok:
        raise HTTPException(404, "article not found")

    # Если body изменился — переcборка чанков + индексов
    if "body" in payload:
        art = await repo.get(article_id)
        assert art is not None
        # Старые чанки чистим из индексов
        old_chunks = await repo.list_chunks(article_id)
        old_ids = [f"{article_id}:{c.chunk_order}" for c in old_chunks]
        await delete_article_index(
            article_id=article_id,
            chunk_ids=old_ids,
            vector_store=vector_store,
            text_search=text_search,
        )
        # Пересборка
        new_chunks = chunk_text(art.body)
        await repo.replace_chunks(
            article_id,
            [
                {"text": c.text, "section_title": c.section_title, "chunk_order": c.chunk_order}
                for c in new_chunks
            ],
        )
        await session.commit()

        if new_chunks:
            from adapters.vector_store.base import VectorRecord
            from adapters.text_search.base import TextSearchRecord

            texts = [c.text for c in new_chunks]
            vectors = await embeddings.embed_documents(texts)
            try:
                await vector_store.upsert(
                    [
                        VectorRecord(
                            id=f"kb:{article_id}:{c.chunk_order}",
                            target_type="kb_chunk",
                            target_id=f"{article_id}:{c.chunk_order}",
                            text=c.text,
                            metadata={
                                "article_id": article_id,
                                "article_title": art.title,
                                "section_title": c.section_title or "",
                                "module": art.module or "",
                            },
                            vector=v,
                        )
                        for c, v in zip(new_chunks, vectors, strict=True)
                    ]
                )
                await text_search.upsert(
                    [
                        TextSearchRecord(
                            id=f"kb:{article_id}:{c.chunk_order}",
                            target_type="kb_chunk",
                            target_id=f"{article_id}:{c.chunk_order}",
                            title=(art.title + (" — " + c.section_title if c.section_title else ""))[:200],
                            content=c.text,
                        )
                        for c in new_chunks
                    ]
                )
            except Exception:  # noqa: BLE001
                pass
    else:
        await session.commit()

    art = await repo.get(article_id)
    assert art is not None
    return _serialize(art)


@router.post("/bulk")
async def bulk_ingest(
    _user_id: Annotated[str, Depends(get_user_id)],
    file: UploadFile = File(...),
    kind: str = Form("markdown"),
    module: str | None = Form(None),
    embeddings=Depends(embeddings_client),
    vector_store=Depends(vector_store_client),
    text_search=Depends(text_search_client),
) -> dict[str, Any]:
    """Принимает zip-архив или одиночный md/html файл и прогоняет KB pipeline."""
    if kind not in {"markdown", "html"}:
        raise HTTPException(422, "kind must be 'markdown' or 'html'")

    raw = await file.read()
    suffix = (file.filename or "").lower()
    is_zip = suffix.endswith(".zip")
    is_single = suffix.endswith((".md", ".markdown", ".html", ".htm"))
    if not (is_zip or is_single):
        raise HTTPException(422, "expected .zip, .md, or .html")

    with tempfile.TemporaryDirectory(prefix="kb_bulk_") as tmpdir:
        root = Path(tmpdir)
        if is_zip:
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    for member in zf.infolist():
                        # защита от path traversal
                        target = (root / member.filename).resolve()
                        if not str(target).startswith(str(root.resolve())):
                            continue
                        if member.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(target, "wb") as dst:
                            dst.write(src.read())
            except zipfile.BadZipFile as e:
                raise HTTPException(422, f"bad zip: {e}") from e
            source_path = str(root)
        else:
            path = root / (file.filename or "article.md")
            path.write_bytes(raw)
            source_path = str(path)

        pipeline = KBIngestionPipeline(
            embeddings=embeddings,
            vector_store=vector_store,
            text_search=text_search,
            session_factory=_session_factory(),
            default_module=module,
        )
        stats = await pipeline.run(source_path, kind=kind)
    return {"status": "ok", "stats": stats}


@router.delete("/{article_id}")
async def delete_article(
    article_id: str,
    session: SessionDep,
    vector_store=Depends(vector_store_client),
    text_search=Depends(text_search_client),
) -> dict[str, str]:
    repo = KBRepository(session)
    chunks = await repo.list_chunks(article_id)
    chunk_ids = [f"{article_id}:{c.chunk_order}" for c in chunks]
    ok = await repo.delete(article_id)
    if not ok:
        raise HTTPException(404, "article not found")
    await session.commit()
    await delete_article_index(
        article_id=article_id,
        chunk_ids=chunk_ids,
        vector_store=vector_store,
        text_search=text_search,
    )
    return {"status": "ok"}
