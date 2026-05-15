"""Доменные Pydantic-модели.

Добавляются по мере необходимости. На текущий момент:

- ``Ticket`` / ``TicketComment`` — этап 5 (PII).
- ``TicketSummary`` — этап 7 (ingest).

Остальные модели из ``docs/03-DATA-MODELS.md`` появятся, когда их начнут
использовать сервисы.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TicketComment(BaseModel):
    author_role: str | None = None
    content: str
    created_at: datetime
    is_internal: bool = False


class Ticket(BaseModel):
    id: str
    external_id: str
    channel: Literal["email", "messenger", "chatbot", "sm", "phone", "other"]
    category: str | None = None
    module: str | None = None
    subject: str
    description: str
    conversation: list[TicketComment] = Field(default_factory=list)
    author_role: str | None = None
    assignee: str | None = None
    status: Literal["open", "in_progress", "resolved", "closed", "cancelled"]
    priority: Literal["low", "normal", "high", "critical"] | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    closed_at: datetime | None = None
    raw_fields: dict[str, Any] = Field(default_factory=dict)


class TicketSummary(BaseModel):
    """LLM-выжимка решённого тикета."""

    ticket_id: str
    summary_one_line: str
    symptom: str
    root_cause: str | None = None
    solution_steps: list[str] = Field(default_factory=list)
    affected_module: str | None = None
    user_role: str | None = None
    is_known_issue: bool = False
    resolution_status: Literal["resolved", "no_resolution", "workaround", "unclear"]
    is_duplicate_of: str | None = None
    generated_at: datetime
    model_used: str


SourceType = Literal[
    "kb_article",
    "kb_chunk",
    "ticket_summary",
    "ticket_symptom",
    "ticket_full",
    "playbook",
]


class Source(BaseModel):
    """Найденный retriever-ом источник, который попадает в промпт."""

    source_type: SourceType
    source_id: str
    title: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0
    rank: int = 0
    # debug-поля: показываются на фронте под карточкой источника
    vector_score: float | None = None
    text_score: float | None = None
    vector_rank: int | None = None
    text_rank: int | None = None
    retrieval_source: str | None = None  # "vector" | "fts" | "both"


class Citation(BaseModel):
    """Ссылка [N] в ответе ассистента."""

    source_index: int
    source: Source


class Answer(BaseModel):
    """Полный ответ RAG-ассистента."""

    text: str
    citations: list[Citation] = Field(default_factory=list)
    used_sources: list[Source] = Field(default_factory=list)
    model_used: str
    latency_ms: int
    token_usage: dict[str, int | None] | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    clarify_question: str | None = None


class TicketContext(BaseModel):
    """Контекст тикета — если ассистент вызывается из карточки тикета."""

    ticket_id: str | None = None
    subject: str | None = None
    description: str | None = None
    module: str | None = None
    category: str | None = None


class AssistantRequest(BaseModel):
    query: str
    conversation_id: str | None = None
    ticket_context: TicketContext | None = None
    filters: dict[str, Any] | None = None
    stream: bool = False
    allow_clarify: bool = False


class AssistantChunk(BaseModel):
    """Кусок streaming-ответа: либо метаданные источников, либо delta, либо итог."""

    type: Literal["sources", "delta", "final", "error"]
    delta: str | None = None
    sources: list[Source] | None = None
    answer: Answer | None = None
    error: str | None = None
    request_id: str | None = None


class Categorization(BaseModel):
    """Результат автокатегоризации входящего обращения."""

    category: str
    module: str | None = None
    type: Literal[
        "bug",
        "question",
        "access_request",
        "feature_request",
        "incident",
        "duplicate",
        "other",
    ] = "other"
    urgency: Literal["low", "normal", "high", "critical"] = "normal"
    confidence: float = 0.0
    suggested_assignee_group: str | None = None
    extracted_application_id: str | None = None
    reasoning: str = ""


EdgeCaseType = Literal["typical", "no_answer_in_kb", "ambiguous", "adversarial"]


class EvalCase(BaseModel):
    """Эталонный кейс для прогона evals."""

    case_id: str
    category: str
    query: str
    ticket_context: dict[str, Any] | None = None
    expected_sources: list[str] = Field(default_factory=list)
    must_mention: list[str] = Field(default_factory=list)
    must_not_mention: list[str] = Field(default_factory=list)
    expected_answer_summary: str = ""
    edge_case_type: EdgeCaseType = "typical"
