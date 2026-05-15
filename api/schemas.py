"""Pydantic-схемы запросов/ответов API.

Принципы:

- На вход — отдельные классы с валидацией (``min_length`` / ``max_length``).
- На выход — переиспользуем доменные модели из ``core.models``, чтобы не
  держать «дублирующие» схемы.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from core.models import TicketContext


class AssistantChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    conversation_id: str | None = None
    ticket_context: TicketContext | None = None
    filters: dict[str, object] | None = None
    allow_clarify: bool = False


class CategorizeBody(BaseModel):
    subject: str = Field(..., min_length=1, max_length=4000)
    description: str = Field(..., min_length=1, max_length=20000)
    channel: str | None = None
    author_role: str | None = None
    attachments: list[str] = Field(default_factory=list)


class CreateConversationBody(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    ticket_id: str | None = None


class FeedbackBody(BaseModel):
    message_id: str
    feedback: Literal[-1, 0, 1]
    comment: str | None = Field(default=None, max_length=1000)


class IngestJobStartedResponse(BaseModel):
    job_id: str
    status: str


class ErrorResponse(BaseModel):
    error: str
    message: str
    details: dict[str, object] | None = None
