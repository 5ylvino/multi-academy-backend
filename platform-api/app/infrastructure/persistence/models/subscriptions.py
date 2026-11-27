"""Platform billing tables: each school's subscription and the invoices the platform sends it."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.sqlalchemy.base import Base


class SubscriptionRow(Base):
    __tablename__ = "platform_subscriptions"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    plan_code: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16))
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        CheckConstraint(
            "status IN ('trialing', 'active', 'past_due', 'cancelled')", name="status_known"
        ),
        CheckConstraint("current_period_end >= current_period_start", name="period_ordered"),
        Index("ix_platform_subscriptions_period_end", "current_period_end"),
    )


class PlatformInvoiceRow(Base):
    __tablename__ = "platform_invoices"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(String(24))
    plan_code: Mapped[str] = mapped_column(String(40))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    student_count: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    base_minor: Mapped[int] = mapped_column(BigInteger)
    per_student_minor: Mapped[int] = mapped_column(BigInteger)
    total_minor: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(8))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settlement_method: Mapped[str | None] = mapped_column(String(16))
    settlement_reference: Mapped[str | None] = mapped_column(String(120))
    settled_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    note: Mapped[str | None] = mapped_column(String(500))
    checkout_reference: Mapped[str | None] = mapped_column(String(40))
    authorization_url: Mapped[str | None] = mapped_column(String(500))
    checkout_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        UniqueConstraint("number", name="uq_platform_invoices_number"),
        CheckConstraint("status IN ('open', 'paid', 'void')", name="status_known"),
        CheckConstraint("total_minor >= 0", name="total_not_negative"),
        CheckConstraint("period_end > period_start", name="period_ordered"),
        CheckConstraint("(status = 'paid') = (paid_at IS NOT NULL)", name="paid_has_date"),
        # One live invoice per period: retries and concurrent cycles cannot double-bill.
        Index(
            "uq_platform_invoices_period",
            "tenant_id",
            "period_start",
            unique=True,
            postgresql_where=text("status <> 'void'"),
        ),
        Index("ix_platform_invoices_tenant", "tenant_id", "id"),
        Index("ix_platform_invoices_open_due", "due_at", postgresql_where=text("status = 'open'")),
    )


class PlatformCheckoutRow(Base):
    """Every online checkout reference started for an invoice, so a payment made on an older
    page is still matched after the school opened a newer one."""

    __tablename__ = "platform_invoice_checkouts"

    reference: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    invoice_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            ["platform_invoices.tenant_id", "platform_invoices.id"],
            ondelete="CASCADE",
        ),
        Index("ix_platform_invoice_checkouts_invoice", "tenant_id", "invoice_id"),
    )
