from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

DUAL_ACTIONS = (
    "tenant.blacklist",
    "tenant.unblacklist",
    "tenant.destroy",
    "tenant.offboard",
    "flag.kill_switch",
    # Lifting a kill switch re-exposes a feature that was disabled for a reason,
    # so it needs the same two-person approval as setting one.
    "flag.kill_switch_lift",
    "provider.switch_live",
    # Moving a live provider back to test/sandbox stops real traffic reaching it.
    "provider.switch_from_live",
)
DUAL_STATUSES = ("pending", "approved", "rejected", "executed", "expired", "cancelled")


class DualControlRequest(Base):
    """Two-person approval for destructive / high-risk control actions."""

    __tablename__ = "dual_control_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(64), default="")
    target_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)

    requested_by: Mapped[str] = mapped_column(String(255), default="")
    requested_by_id: Mapped[str] = mapped_column(String(64), default="")
    approved_by: Mapped[str] = mapped_column(String(255), default="")
    approved_by_id: Mapped[str] = mapped_column(String(64), default="")
    decision_reason: Mapped[str] = mapped_column(Text, default="")

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
