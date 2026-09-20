"""SaaS billing invoices + dunning state (Phase B)."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SaasInvoice(Base):
    """Platform SaaS invoice for a tenant subscription period."""

    __tablename__ = "saas_invoices"
    __table_args__ = (
        # One invoice per subscription per billing period. This is what makes
        # invoice generation safe to run concurrently from several replicas.
        UniqueConstraint("subscription_id", "period_key", name="uq_invoice_subscription_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("subscriptions.id"), nullable=True, index=True
    )
    number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Deterministic period identifier, e.g. 2026-07 or 2026 for yearly cycles.
    period_key: Mapped[str] = mapped_column(String(32), default="", index=True)
    amount_minor: Mapped[int] = mapped_column(Integer, default=0)
    amount_paid_minor: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    # draft | open | partially_paid | paid | past_due | void | written_off
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Tax / VAT (minor units); total amount_minor includes tax.
    tax_minor: Mapped[int] = mapped_column(Integer, default=0)
    tax_label: Mapped[str] = mapped_column(String(64), default="VAT")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def balance_minor(self) -> int:
        return max(0, int(self.amount_minor or 0) - int(self.amount_paid_minor or 0))


class SaasPayment(Base):
    """Recorded SaaS payment against an invoice (manual or gateway)."""

    __tablename__ = "saas_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("saas_invoices.id"), index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    amount_minor: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    method: Mapped[str] = mapped_column(String(32), default="manual")  # manual | transfer | bank
    provider_id: Mapped[str] = mapped_column(String(64), default="")
    # Unique when present so a retried gateway webhook cannot double-credit.
    provider_reference: Mapped[str] = mapped_column(String(128), default="", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, index=True
    )
    recorded_by: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
