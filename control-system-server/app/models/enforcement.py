from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

BLOCK_TARGET_TYPES = ("user", "email", "phone", "ip", "device", "capability")


class Block(Base):
    """Block a user / email / IP / device globally or per tenant, or freeze a
    capability (feature key forced off) for one tenant."""

    __tablename__ = "blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_type: Mapped[str] = mapped_column(String(16), index=True)
    # user id, email, phone, IP/CIDR, WebAuthn credential id, or feature key
    value: Mapped[str] = mapped_column(String(255), index=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(32), default="deny")  # deny | throttle | captcha
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(16), default="info")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Empty list = all tenants
    tenant_ids: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
