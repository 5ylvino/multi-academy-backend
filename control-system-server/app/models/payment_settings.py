"""Per-tenant payment button and gateway settings — managed only from the control console."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TenantPaymentContextBinding(Base):
    """Enable/disable a Pay button and optionally pin a gateway for a payment type."""

    __tablename__ = "tenant_payment_context_bindings"
    __table_args__ = (UniqueConstraint("tenant_id", "context_key", name="uq_tenant_payment_context"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    context_key: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    gateway_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TenantPaymentGatewayToggle(Base):
    """Enable or disable a gateway for a tenant."""

    __tablename__ = "tenant_payment_gateway_toggles"
    __table_args__ = (UniqueConstraint("tenant_id", "gateway_id", name="uq_tenant_payment_gateway"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    gateway_id: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
