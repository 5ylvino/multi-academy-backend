"""tenant install credentials and trials

Revision ID: 6f1a2b3c4d5e
Revises: 5e8a7c1d4b2f
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "6f1a2b3c4d5e"
down_revision: Union[str, Sequence[str], None] = "5e8a7c1d4b2f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_installs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("install_token_hash", sa.String(64), nullable=False),
        sa.Column("device_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("token_issued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_installs_tenant"),
        sa.UniqueConstraint("install_token_hash", name="uq_tenant_installs_token_hash"),
    )
    op.create_index("ix_tenant_installs_tenant_id", "tenant_installs", ["tenant_id"])
    op.create_index("ix_tenant_installs_install_token_hash", "tenant_installs", ["install_token_hash"])
    op.create_index("ix_tenant_installs_device_hash", "tenant_installs", ["device_hash"])
    op.create_table(
        "tenant_trials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reminder_7_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminder_1_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_trials_tenant"),
    )
    op.create_index("ix_tenant_trials_tenant_id", "tenant_trials", ["tenant_id"])
    op.create_index("ix_tenant_trials_enabled", "tenant_trials", ["enabled"])
    op.create_index("ix_tenant_trials_status", "tenant_trials", ["status"])
    op.create_index("ix_tenant_trials_ends_at", "tenant_trials", ["ends_at"])


def downgrade() -> None:
    op.drop_table("tenant_trials")
    op.drop_table("tenant_installs")
