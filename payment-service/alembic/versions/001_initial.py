"""Initial payment service tables.

Revision ID: 001_initial
Revises:
Create Date: 2026-09-19
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
        "payment_checkout_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("context_key", sa.String(length=64), nullable=False),
        sa.Column("gateway_id", sa.String(length=32), nullable=False),
        sa.Column("reference", sa.String(length=128), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="NGN"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("callback_url", sa.Text(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("provider_reference", sa.String(length=128), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference"),
    )
    op.create_index("ix_payment_checkout_sessions_tenant_id", "payment_checkout_sessions", ["tenant_id"])

    op.create_table(
        "payment_context_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("context_key", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("gateway_id", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "context_key", name="uq_context_binding"),
    )
    op.create_index("ix_payment_context_bindings_tenant_id", "payment_context_bindings", ["tenant_id"])

    op.create_table(
        "payment_gateway_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("gateway_id", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "gateway_id", name="uq_gateway_override"),
    )
    op.create_index("ix_payment_gateway_overrides_tenant_id", "payment_gateway_overrides", ["tenant_id"])

    op.create_table(
        "payment_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=True),
        sa.Column("gateway_id", sa.String(length=32), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("reference", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key"),
    )
    op.create_index("ix_payment_webhook_events_tenant_id", "payment_webhook_events", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("payment_webhook_events")
    op.drop_table("payment_gateway_overrides")
    op.drop_table("payment_context_bindings")
    op.drop_table("payment_checkout_sessions")
