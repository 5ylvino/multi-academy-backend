"""persist explicit school-runtime initialization jobs

Revision ID: 2f6d9c1a4b7e
Revises: c91a7e3f5d20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2f6d9c1a4b7e"
down_revision: Union[str, Sequence[str], None] = "c91a7e3f5d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenant_provisioning_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column(
            "school_db_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("defaults_version", sa.String(length=32), nullable=False, server_default="v1"),
        sa.Column("requested_by", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("requested_by_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("result", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
    )
    op.create_index(
        "ix_tenant_provisioning_jobs_tenant_id",
        "tenant_provisioning_jobs",
        ["tenant_id"],
    )
    op.create_index(
        "ix_tenant_provisioning_jobs_status",
        "tenant_provisioning_jobs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_provisioning_jobs_status", table_name="tenant_provisioning_jobs")
    op.drop_index("ix_tenant_provisioning_jobs_tenant_id", table_name="tenant_provisioning_jobs")
    op.drop_table("tenant_provisioning_jobs")
