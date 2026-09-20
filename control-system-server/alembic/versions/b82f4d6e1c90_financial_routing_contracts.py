"""add fee split and payroll routing contracts

Revision ID: b82f4d6e1c90
Revises: 9c6e2f4a1b7d
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b82f4d6e1c90"
down_revision: Union[str, Sequence[str], None] = "9c6e2f4a1b7d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fee_split_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("fee_type", sa.String(length=64), nullable=False, server_default="school_fees"),
        sa.Column("provider_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="NGN"),
        sa.Column("allocations", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "fee_type", name="uq_fee_split_tenant_type"),
    )
    op.create_index("ix_fee_split_rules_tenant_id", "fee_split_rules", ["tenant_id"])
    op.create_table(
        "payroll_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("account_role", sa.String(length=32), nullable=False, server_default="salary"),
        sa.Column("provider_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("account_reference", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="NGN"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "account_role", name="uq_payroll_tenant_role"),
    )
    op.create_index("ix_payroll_accounts_tenant_id", "payroll_accounts", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_payroll_accounts_tenant_id", table_name="payroll_accounts")
    op.drop_table("payroll_accounts")
    op.drop_index("ix_fee_split_rules_tenant_id", table_name="fee_split_rules")
    op.drop_table("fee_split_rules")
