"""Break-glass support sessions — ticket-bound, time-boxed (Phase B)."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SupportSession(Base):
    __tablename__ = "support_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    ticket_id: Mapped[str] = mapped_column(String(128), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    staff_email: Mapped[str] = mapped_column(String(255), default="")
    # read | elevated_pii | write_support
    access_level: Mapped[str] = mapped_column(String(32), default="read")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
