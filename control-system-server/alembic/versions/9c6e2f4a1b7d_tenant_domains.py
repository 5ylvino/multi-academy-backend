"""add control-plane tenant domains

Revision ID: 9c6e2f4a1b7d
Revises: 7d4c2e9a1f3b
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9c6e2f4a1b7d"
down_revision: Union[str, Sequence[str], None] = "7d4c2e9a1f3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenant_domains",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="subdomain"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("ssl_status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("hostname", name="uq_tenant_domains_hostname"),
    )
    op.create_index("ix_tenant_domains_tenant_id", "tenant_domains", ["tenant_id"])
    op.create_index("ix_tenant_domains_hostname", "tenant_domains", ["hostname"])


def downgrade() -> None:
    op.drop_index("ix_tenant_domains_hostname", table_name="tenant_domains")
    op.drop_index("ix_tenant_domains_tenant_id", table_name="tenant_domains")
    op.drop_table("tenant_domains")
