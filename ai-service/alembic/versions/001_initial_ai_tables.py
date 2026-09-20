"""Initial AI service tables.

Revision ID: 001_initial
Revises:
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("feature", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_conversations_tenant_id", "ai_conversations", ["tenant_id"])
    op.create_index("ix_ai_conversations_actor_id", "ai_conversations", ["actor_id"])
    op.create_index("ix_ai_conversations_tenant_feature", "ai_conversations", ["tenant_id", "feature"])

    op.create_table(
        "ai_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_messages_conversation_id", "ai_messages", ["conversation_id"])
    op.create_index("ix_ai_messages_tenant_id", "ai_messages", ["tenant_id"])
    op.create_index("ix_ai_messages_conversation_created", "ai_messages", ["conversation_id", "created_at"])

    op.create_table(
        "ai_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("feature", sa.String(length=64), nullable=False),
        sa.Column("provider_id", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("sources_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_audit_events_tenant_id", "ai_audit_events", ["tenant_id"])
    op.create_index("ix_ai_audit_events_actor_id", "ai_audit_events", ["actor_id"])
    op.create_index("ix_ai_audit_events_request_id", "ai_audit_events", ["request_id"])
    op.create_index("ix_ai_audit_tenant_created", "ai_audit_events", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_audit_tenant_created", table_name="ai_audit_events")
    op.drop_index("ix_ai_audit_events_request_id", table_name="ai_audit_events")
    op.drop_index("ix_ai_audit_events_actor_id", table_name="ai_audit_events")
    op.drop_index("ix_ai_audit_events_tenant_id", table_name="ai_audit_events")
    op.drop_table("ai_audit_events")

    op.drop_index("ix_ai_messages_conversation_created", table_name="ai_messages")
    op.drop_index("ix_ai_messages_tenant_id", table_name="ai_messages")
    op.drop_index("ix_ai_messages_conversation_id", table_name="ai_messages")
    op.drop_table("ai_messages")

    op.drop_index("ix_ai_conversations_tenant_feature", table_name="ai_conversations")
    op.drop_index("ix_ai_conversations_actor_id", table_name="ai_conversations")
    op.drop_index("ix_ai_conversations_tenant_id", table_name="ai_conversations")
    op.drop_table("ai_conversations")
