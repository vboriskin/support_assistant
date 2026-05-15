"""Conversations: list / create / detail / feedback."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import conversations_repo, get_user_id
from api.schemas import CreateConversationBody, FeedbackBody
from db.repositories.conversations import ConversationsRepository

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _conv_to_dict(c) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "id": c.id,
        "user_id": c.user_id,
        "ticket_id": c.ticket_id,
        "title": c.title,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }


def _msg_to_dict(m) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "id": m.id,
        "conversation_id": m.conversation_id,
        "role": m.role,
        "content": m.content,
        "citations": m.citations_json,
        "used_sources": m.used_sources_json,
        "feedback": m.feedback,
        "feedback_comment": m.feedback_comment,
        "created_at": m.created_at.isoformat(),
    }


@router.get("")
async def list_conversations(
    user_id: Annotated[str, Depends(get_user_id)],
    repo: Annotated[ConversationsRepository, Depends(conversations_repo)],
    limit: int = 30,
) -> list[dict[str, Any]]:
    items = await repo.list_by_user(user_id, limit=limit)
    return [_conv_to_dict(c) for c in items]


@router.post("")
async def create_conversation(
    body: CreateConversationBody,
    user_id: Annotated[str, Depends(get_user_id)],
    repo: Annotated[ConversationsRepository, Depends(conversations_repo)],
) -> dict[str, Any]:
    conv = await repo.create(user_id=user_id, title=body.title, ticket_id=body.ticket_id)
    await repo.session.commit()
    return _conv_to_dict(conv)


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    repo: Annotated[ConversationsRepository, Depends(conversations_repo)],
) -> dict[str, Any]:
    conv = await repo.get(conversation_id, with_messages=True)
    if conv is None:
        raise HTTPException(404, detail="conversation not found")
    return {
        **_conv_to_dict(conv),
        "messages": [_msg_to_dict(m) for m in conv.messages],
    }


@router.post("/{conversation_id}/feedback")
async def submit_feedback(
    conversation_id: str,
    body: FeedbackBody,
    repo: Annotated[ConversationsRepository, Depends(conversations_repo)],
) -> dict[str, str]:
    ok = await repo.set_feedback(body.message_id, feedback=body.feedback, comment=body.comment)
    if not ok:
        raise HTTPException(404, detail="message not found")
    await repo.session.commit()
    return {"status": "ok"}
