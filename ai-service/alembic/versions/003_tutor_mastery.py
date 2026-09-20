"""Tutor sessions and topic mastery.

Revision ID: 003_tutor_mastery
Revises: 002_documents
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003_tutor_mastery"
down_revision: Union[str, Sequence[str], None] = "002_documents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_tutor_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("student_id", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=True),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="explain"),
        sa.Column("mastery_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quiz_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_tutor_sessions_tenant_student", "ai_tutor_sessions", ["tenant_id", "student_id"])

    op.create_table(
        "ai_tutor_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("message_type", sa.String(length=16), nullable=False, server_default="chat"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_tutor_messages_session", "ai_tutor_messages", ["session_id", "created_at"])

    op.create_table(
        "ai_topic_mastery",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("student_id", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=True),
        sa.Column("topic_key", sa.String(length=255), nullable=False),
        sa.Column("mastery_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "student_id", "subject_id", "topic_key", name="uq_topic_mastery"),
    )
    op.create_index("ix_ai_topic_mastery_tenant_student", "ai_topic_mastery", ["tenant_id", "student_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_topic_mastery_tenant_student", table_name="ai_topic_mastery")
    op.drop_table("ai_topic_mastery")
    op.drop_index("ix_ai_tutor_messages_session", table_name="ai_tutor_messages")
    op.drop_table("ai_tutor_messages")
    op.drop_index("ix_ai_tutor_sessions_tenant_student", table_name="ai_tutor_sessions")
    op.drop_table("ai_tutor_sessions")
