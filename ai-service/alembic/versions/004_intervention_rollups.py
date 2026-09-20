"""Intervention plans and class mastery rollups.

Revision ID: 004_intervention_rollups
Revises: 003_tutor_mastery
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004_intervention_rollups"
down_revision: Union[str, Sequence[str], None] = "003_tutor_mastery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_topic_mastery_rollups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("class_id", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=True),
        sa.Column("topic_key", sa.String(length=255), nullable=False),
        sa.Column("student_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("below_threshold_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_mastery_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "class_id", "subject_id", "topic_key", name="uq_mastery_rollup"),
    )
    op.create_index(
        "ix_ai_mastery_rollups_tenant_class",
        "ai_topic_mastery_rollups",
        ["tenant_id", "class_id"],
    )

    op.create_table(
        "ai_intervention_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("teacher_id", sa.String(length=64), nullable=False),
        sa.Column("class_id", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=True),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("plan_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("approved_by", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_intervention_tenant_teacher",
        "ai_intervention_plans",
        ["tenant_id", "teacher_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_intervention_tenant_teacher", table_name="ai_intervention_plans")
    op.drop_table("ai_intervention_plans")
    op.drop_index("ix_ai_mastery_rollups_tenant_class", table_name="ai_topic_mastery_rollups")
    op.drop_table("ai_topic_mastery_rollups")
