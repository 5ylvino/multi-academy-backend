"""Usage meters + events for FinOps / quotas (Phase B)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UsageMeter(Base):
    """Named meter definition (e.g. sms_segments, ai_tokens, gateway_checkouts)."""

    __tablename__ = "usage_meters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    unit: Mapped[str] = mapped_column(String(32), default="count")
    # Optional feature_key this meter maps to for quota enforcement
    feature_key: Mapped[str] = mapped_column(String(64), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageEvent(Base):
    """Ingested usage event from Nest (or staff adjustment)."""

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    meter_key: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    # Optional idempotency / correlation
    event_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    properties: Mapped[str] = mapped_column(Text, default="")  # JSON string
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageRollup(Base):
    """Per-tenant per-meter period totals (soft cache for dashboards)."""

    __tablename__ = "usage_rollups"
    __table_args__ = (UniqueConstraint("tenant_id", "meter_key", "period_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    meter_key: Mapped[str] = mapped_column(String(64), index=True)
    period_key: Mapped[str] = mapped_column(String(16), index=True)  # YYYY-MM
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
