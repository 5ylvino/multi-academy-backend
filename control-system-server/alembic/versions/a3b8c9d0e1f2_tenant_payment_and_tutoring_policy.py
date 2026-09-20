"""tenant payment settings and tutoring policy

Revision ID: a3b8c9d0e1f2
Revises: 6f1a2b3c4d5e
Create Date: 2026-09-19

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3b8c9d0e1f2"
down_revision: Union[str, None] = "6f1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenant_payment_context_bindings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("context_key", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("gateway_id", sa.String(length=32), nullable=True),
        sa.Column("updated_by", sa.String(length=255), server_default="", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "context_key", name="uq_tenant_payment_context"),
    )
    op.create_index(
        "ix_tenant_payment_context_bindings_tenant_id",
        "tenant_payment_context_bindings",
        ["tenant_id"],
    )

    op.create_table(
        "tenant_payment_gateway_toggles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("gateway_id", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("updated_by", sa.String(length=255), server_default="", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "gateway_id", name="uq_tenant_payment_gateway"),
    )
    op.create_index(
        "ix_tenant_payment_gateway_toggles_tenant_id",
        "tenant_payment_gateway_toggles",
        ["tenant_id"],
    )

    op.create_table(
        "tutoring_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("platform_fee_percent", sa.Float(), server_default="15", nullable=False),
        sa.Column("default_currency", sa.String(length=8), server_default="NGN", nullable=False),
        sa.Column("allow_external_tutors", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("marketplace_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("updated_by", sa.String(length=255), server_default="", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_tutoring_policy_tenant"),
    )
    op.create_index("ix_tutoring_policies_tenant_id", "tutoring_policies", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_tutoring_policies_tenant_id", table_name="tutoring_policies")
    op.drop_table("tutoring_policies")
    op.drop_index("ix_tenant_payment_gateway_toggles_tenant_id", table_name="tenant_payment_gateway_toggles")
    op.drop_table("tenant_payment_gateway_toggles")
    op.drop_index("ix_tenant_payment_context_bindings_tenant_id", table_name="tenant_payment_context_bindings")
    op.drop_table("tenant_payment_context_bindings")
