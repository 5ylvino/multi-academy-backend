from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PaymentCheckoutSession(Base):
    __tablename__ = "payment_checkout_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    context_key: Mapped[str] = mapped_column(String(64), nullable=False)
    gateway_id: Mapped[str] = mapped_column(String(32), nullable=False)
    reference: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="NGN")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    callback_url: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentContextBinding(Base):
    """Per-tenant hook: which gateway a payment button uses, and whether it is on."""

    __tablename__ = "payment_context_bindings"
    __table_args__ = (UniqueConstraint("tenant_id", "context_key", name="uq_context_binding"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    context_key: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    gateway_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PaymentGatewayOverride(Base):
    """Enable or disable a gateway for a tenant (independent of control-plane default)."""

    __tablename__ = "payment_gateway_overrides"
    __table_args__ = (UniqueConstraint("tenant_id", "gateway_id", name="uq_gateway_override"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    gateway_id: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PaymentWebhookEvent(Base):
    __tablename__ = "payment_webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    gateway_id: Mapped[str] = mapped_column(String(32), nullable=False)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
