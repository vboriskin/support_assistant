"""initial schema: tickets, summaries, kb, conversations, messages, llm logs, ingest jobs

Revision ID: 0001
Revises:
Create Date: 2026-05-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tickets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("module", sa.String(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("conversation_json", sa.JSON(), nullable=False),
        sa.Column("author_role", sa.String(), nullable=True),
        sa.Column("assignee", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("priority", sa.String(), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("raw_fields_json", sa.JSON(), nullable=True),
        sa.Column("is_pii_masked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("masked_at", sa.DateTime(), nullable=True),
        sa.Column("pii_audit_json", sa.JSON(), nullable=True),
        sa.Column("indexed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("external_id", name="uq_tickets_external_id"),
    )
    op.create_index("ix_tickets_external_id", "tickets", ["external_id"])
    op.create_index("ix_tickets_category", "tickets", ["category"])
    op.create_index("ix_tickets_module", "tickets", ["module"])
    op.create_index("ix_tickets_created_at", "tickets", ["created_at"])

    op.create_table(
        "ticket_summaries",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("ticket_id", sa.String(), nullable=False),
        sa.Column("summary_one_line", sa.Text(), nullable=False),
        sa.Column("symptom", sa.Text(), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("solution_steps_json", sa.JSON(), nullable=False),
        sa.Column("affected_module", sa.String(), nullable=True),
        sa.Column("user_role", sa.String(), nullable=True),
        sa.Column("is_known_issue", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resolution_status", sa.String(), nullable=False),
        sa.Column("is_duplicate_of", sa.String(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("model_used", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], ondelete="CASCADE", name="fk_summaries_ticket"
        ),
        sa.ForeignKeyConstraint(
            ["is_duplicate_of"], ["tickets.id"], name="fk_summaries_duplicate_of"
        ),
        sa.UniqueConstraint("ticket_id", name="uq_summaries_ticket_id"),
    )
    op.create_index("ix_summaries_module", "ticket_summaries", ["affected_module"])
    op.create_index("ix_summaries_resolution", "ticket_summaries", ["resolution_status"])
    op.create_index(
        "idx_summaries_module_resolution",
        "ticket_summaries",
        ["affected_module", "resolution_status"],
    )

    op.create_table(
        "kb_articles",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("audience", sa.String(), nullable=False),
        sa.Column("module", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("tags_json", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_path", sa.String(), nullable=True),
        sa.Column("is_deprecated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_kb_articles_module", "kb_articles", ["module"])

    op.create_table(
        "kb_chunks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("article_id", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("section_title", sa.String(), nullable=True),
        sa.Column("chunk_order", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["article_id"], ["kb_articles.id"], ondelete="CASCADE", name="fk_chunks_article"
        ),
    )
    op.create_index("ix_kb_chunks_article_id", "kb_chunks", ["article_id"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("ticket_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], name="fk_conversations_ticket"),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations_json", sa.JSON(), nullable=True),
        sa.Column("used_sources_json", sa.JSON(), nullable=True),
        sa.Column("feedback", sa.Integer(), nullable=True),
        sa.Column("feedback_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
            name="fk_messages_conversation",
        ),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    op.create_table(
        "llm_call_logs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("prompt_hash", sa.String(), nullable=False),
        sa.Column("prompt_preview", sa.Text(), nullable=True),
        sa.Column("response_preview", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_llm_call_logs_purpose", "llm_call_logs", ["purpose"])
    op.create_index("ix_llm_call_logs_created_at", "llm_call_logs", ["created_at"])

    op.create_table(
        "ingest_jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("job_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=True),
        sa.Column("processed_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
    )
    op.create_index("ix_ingest_jobs_status", "ingest_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_ingest_jobs_status", table_name="ingest_jobs")
    op.drop_table("ingest_jobs")

    op.drop_index("ix_llm_call_logs_created_at", table_name="llm_call_logs")
    op.drop_index("ix_llm_call_logs_purpose", table_name="llm_call_logs")
    op.drop_table("llm_call_logs")

    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")

    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_table("conversations")

    op.drop_index("ix_kb_chunks_article_id", table_name="kb_chunks")
    op.drop_table("kb_chunks")

    op.drop_index("ix_kb_articles_module", table_name="kb_articles")
    op.drop_table("kb_articles")

    op.drop_index("idx_summaries_module_resolution", table_name="ticket_summaries")
    op.drop_index("ix_summaries_resolution", table_name="ticket_summaries")
    op.drop_index("ix_summaries_module", table_name="ticket_summaries")
    op.drop_table("ticket_summaries")

    op.drop_index("ix_tickets_created_at", table_name="tickets")
    op.drop_index("ix_tickets_module", table_name="tickets")
    op.drop_index("ix_tickets_category", table_name="tickets")
    op.drop_index("ix_tickets_external_id", table_name="tickets")
    op.drop_table("tickets")
