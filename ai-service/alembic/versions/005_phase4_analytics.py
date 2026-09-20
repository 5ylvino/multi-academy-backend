"""Phase 4: report comment drafts and retention markers.

Revision ID: 005_phase4_analytics
Revises: 004_intervention_rollups
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005_phase4_analytics"
down_revision: Union[str, Sequence[str], None] = "004_intervention_rollups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_report_comment_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("student_id", sa.String(length=64), nullable=False),
        sa.Column("term_id", sa.String(length=64), nullable=True),
        sa.Column("subject_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("comment_text", sa.Text(), nullable=False),
        sa.Column("comment_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("approved_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_report_comments_tenant_student",
        "ai_report_comment_drafts",
        ["tenant_id", "student_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_report_comments_tenant_student", table_name="ai_report_comment_drafts")
    op.drop_table("ai_report_comment_drafts")
