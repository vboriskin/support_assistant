"""Prompt playground: версии системных промптов + preview ответа.

POST /api/prompts/preview — ad-hoc генерация ответа с подменённым system prompt
(для UI-сравнения «до/после»). Запись в БД и в historic-logs не делается.

CRUD /api/prompts — версии. ``is_active=true`` — текущая. Активация атомарная.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from adapters.llm.base import ChatMessage, LLMClient
from api.dependencies import (
    SessionDep,
    assistant_service,
    get_user_id,
    llm_client,
    retrieval_service,
)
from config.logging import get_logger
from core.models import TicketContext
from core.prompts.loader import load_prompt
from db.models import PromptVersion
from services.assistant import AssistantService
from services.retrieval import RetrievalFilters, RetrievalService

logger = get_logger("api.prompts")
router = APIRouter(prefix="/prompts", tags=["prompts"])


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _to_dict(p: PromptVersion) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "content": p.content,
        "note": p.note,
        "is_active": p.is_active,
        "created_by": p.created_by,
        "created_at": p.created_at.isoformat(),
    }


# ----------------------------------------------------------------------
# CRUD prompt versions
# ----------------------------------------------------------------------


class PromptVersionCreate(BaseModel):
    name: str = Field(default="system_assistant", min_length=1, max_length=80)
    content: str = Field(..., min_length=10, max_length=20000)
    note: str | None = Field(default=None, max_length=1000)
    activate: bool = False


@router.get("")
async def list_versions(session: SessionDep, name: str = "system_assistant") -> dict[str, Any]:
    """Список версий + текущий «системный» prompt из файла (как baseline)."""
    rows = (
        await session.execute(
            select(PromptVersion).where(PromptVersion.name == name).order_by(PromptVersion.created_at.desc())
        )
    ).scalars().all()
    try:
        baseline = load_prompt(name)
    except FileNotFoundError:
        baseline = ""
    return {
        "name": name,
        "baseline_content": baseline,
        "versions": [_to_dict(p) for p in rows],
    }


@router.post("")
async def create_version(
    body: PromptVersionCreate,
    session: SessionDep,
    user_id: Annotated[str, Depends(get_user_id)],
) -> dict[str, Any]:
    version_id = str(uuid.uuid4())
    p = PromptVersion(
        id=version_id,
        name=body.name,
        content=body.content,
        note=body.note,
        is_active=False,
        created_by=user_id,
        created_at=_now(),
    )
    session.add(p)
    await session.flush()
    if body.activate:
        await session.execute(
            update(PromptVersion).where(PromptVersion.name == body.name).values(is_active=False)
        )
        p.is_active = True
        await session.flush()
    await session.commit()
    return _to_dict(p)


@router.post("/{version_id}/activate")
async def activate_version(version_id: str, session: SessionDep) -> dict[str, Any]:
    p = await session.get(PromptVersion, version_id)
    if p is None:
        raise HTTPException(404, "version not found")
    await session.execute(
        update(PromptVersion).where(PromptVersion.name == p.name).values(is_active=False)
    )
    p.is_active = True
    await session.flush()
    await session.commit()
    return _to_dict(p)


@router.delete("/{version_id}")
async def delete_version(version_id: str, session: SessionDep) -> dict[str, str]:
    p = await session.get(PromptVersion, version_id)
    if p is None:
        raise HTTPException(404, "version not found")
    if p.is_active:
        raise HTTPException(400, "cannot delete active version")
    await session.delete(p)
    await session.commit()
    return {"status": "ok"}


# ----------------------------------------------------------------------
# Preview — генерация ответа с подменённым system prompt
# ----------------------------------------------------------------------


class PromptPreviewRequest(BaseModel):
    system_prompt: str = Field(..., min_length=10, max_length=20000)
    query: str = Field(..., min_length=1, max_length=4000)
    ticket_context: dict[str, Any] | None = None
    case_id: str | None = None  # если передан — берём query/ctx из eval-кейса


_CASES_DIR_NAME = "evals/cases"


def _load_case(case_id: str) -> dict[str, Any] | None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / _CASES_DIR_NAME
    for p in root.rglob("*.json"):
        try:
            import json as _json

            data = _json.loads(p.read_text(encoding="utf-8"))
            if data.get("case_id") == case_id:
                return data
        except Exception:
            continue
    return None


@router.post("/preview")
async def preview(
    body: PromptPreviewRequest,
    _user_id: Annotated[str, Depends(get_user_id)],
    retrieval: Annotated[RetrievalService, Depends(retrieval_service)],
    llm: Annotated[LLMClient, Depends(llm_client)],
    _assistant: Annotated[AssistantService, Depends(assistant_service)],
) -> dict[str, Any]:
    """Ad-hoc ответ ассистента с подменённым системным промптом.

    Под капотом: переиспользуем retrieval (как в обычном answer), но
    собираем messages вручную, чтобы вставить кастомный system prompt.
    """
    case = _load_case(body.case_id) if body.case_id else None
    query = case["query"] if case else body.query
    ctx_data = (case.get("ticket_context") if case else None) or body.ticket_context
    ctx = TicketContext(**ctx_data) if ctx_data else None

    t0 = time.time()
    effective_query = query
    if ctx and ctx.subject:
        effective_query = f"{query} {ctx.subject}"
    retrieval_result = await retrieval.search(effective_query, filters=RetrievalFilters())
    sources = retrieval_result.sources

    # Собираем минимальный prompt: наш system_prompt + контекст + источники + query
    src_block = []
    for i, s in enumerate(sources, start=1):
        src_block.append(f"[{i}] {s.title}\n{s.content}\n---")
    user_content = ""
    if ctx:
        user_content += "=== Текущий тикет ===\n"
        if ctx.subject:
            user_content += f"Тема: {ctx.subject}\n"
        if ctx.description:
            user_content += f"Описание: {ctx.description[:1200]}\n"
    user_content += "\n=== Найденные источники ===\n" + "\n".join(src_block) + "\n"
    user_content += f"\n=== Вопрос ===\n{query}\n"
    user_content += "Ответь по-русски, опираясь только на источники. [1], [2] — ссылки."

    messages = [
        ChatMessage(role="system", content=body.system_prompt),
        ChatMessage(role="user", content=user_content),
    ]

    try:
        resp = await llm.chat_completion(messages=messages, temperature=0.2, max_tokens=1500)
        latency_ms = int((time.time() - t0) * 1000)
        return {
            "answer_text": resp.text,
            "sources": [s.model_dump(mode="json") for s in sources],
            "latency_ms": latency_ms,
            "model": llm.model_name,
            "case_id": body.case_id,
            "query": query,
        }
    except Exception as e:
        raise HTTPException(500, detail=f"llm error: {e}") from e
