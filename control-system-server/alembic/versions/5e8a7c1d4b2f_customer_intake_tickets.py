"""customer intake tickets

Revision ID: 5e8a7c1d4b2f
Revises: 2f6d9c1a4b7e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "5e8a7c1d4b2f"
down_revision: Union[str, Sequence[str], None] = "2f6d9c1a4b7e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_tickets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_number", sa.String(length=32), nullable=False),
        sa.Column("request_type", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=64), nullable=False),
        sa.Column("organization", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("assigned_to", sa.String(length=255), nullable=False),
        sa.Column("follow_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticket_number"),
    )
    op.create_index("ix_customer_tickets_ticket_number", "customer_tickets", ["ticket_number"], unique=False)
    op.create_index("ix_customer_tickets_request_type", "customer_tickets", ["request_type"], unique=False)
    op.create_index("ix_customer_tickets_email", "customer_tickets", ["email"], unique=False)
    op.create_index("ix_customer_tickets_status", "customer_tickets", ["status"], unique=False)
    op.create_index("ix_customer_tickets_priority", "customer_tickets", ["priority"], unique=False)
    op.create_index("ix_customer_tickets_created_at", "customer_tickets", ["created_at"], unique=False)
    op.create_table(
        "ticket_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("author_email", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["customer_tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ticket_notes_ticket_id", "ticket_notes", ["ticket_id"], unique=False)
    op.create_index("ix_ticket_notes_created_at", "ticket_notes", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_table("ticket_notes")
    op.drop_table("customer_tickets")
