"""add approval-controlled payroll runs

Revision ID: c91a7e3f5d20
Revises: b82f4d6e1c90
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c91a7e3f5d20"
down_revision: Union[str, Sequence[str], None] = "b82f4d6e1c90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payroll_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("period_key", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("salary_account_id", sa.Integer(), sa.ForeignKey("payroll_accounts.id"), nullable=False),
        sa.Column("gross_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="NGN"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("approved_by", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("external_reference", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "period_key", name="uq_payroll_tenant_period"),
    )
    op.create_index("ix_payroll_runs_tenant_id", "payroll_runs", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_payroll_runs_tenant_id", table_name="payroll_runs")
    op.drop_table("payroll_runs")
