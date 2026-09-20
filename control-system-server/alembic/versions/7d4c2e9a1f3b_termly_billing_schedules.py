"""add optional termly billing schedules

Revision ID: 7d4c2e9a1f3b
Revises: fe907f08ef7b
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7d4c2e9a1f3b"
down_revision: Union[str, Sequence[str], None] = "fe907f08ef7b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("billing_schedule", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "billing_schedule")
