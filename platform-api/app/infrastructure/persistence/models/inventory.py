"""Inventory tables."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.sqlalchemy.base import Base


class StockItemRow(Base):
    __tablename__ = "inventory_items"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    sku: Mapped[str | None] = mapped_column(String(40))
    category: Mapped[str | None] = mapped_column(String(60))
    unit: Mapped[str] = mapped_column(String(16))
    on_hand: Mapped[int] = mapped_column(Integer)
    reorder_level: Mapped[int] = mapped_column(Integer)
    location: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3))
    unit_cost_minor: Mapped[int | None] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        CheckConstraint("on_hand >= 0", name="on_hand_not_negative"),
        CheckConstraint("reorder_level >= 0", name="reorder_not_negative"),
        Index(
            "uq_inventory_items_sku",
            "tenant_id",
            "sku",
            unique=True,
            postgresql_where=text("sku IS NOT NULL"),
        ),
        Index("ix_inventory_items_tenant", "tenant_id", "active", "name"),
    )


class StockMovementRow(Base):
    """Append-only for the app role."""

    __tablename__ = "inventory_movements"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    item_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(12))
    quantity: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    unit_cost_minor: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(String(300))
    issued_to: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    reference: Mapped[str | None] = mapped_column(String(120))
    by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "item_id"],
            ["inventory_items.tenant_id", "inventory_items.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "issued_to"],
            ["school_users.tenant_id", "school_users.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "kind IN ('receive', 'issue', 'return', 'adjust', 'write_off')", name="kind_known"
        ),
        CheckConstraint("quantity <> 0", name="quantity_nonzero"),
        CheckConstraint("balance_after >= 0", name="balance_not_negative"),
        Index("ix_inventory_movements_item", "tenant_id", "item_id", "id"),
        Index(
            "ix_inventory_movements_person",
            "tenant_id",
            "issued_to",
            "id",
            postgresql_where=text("issued_to IS NOT NULL"),
        ),
    )
