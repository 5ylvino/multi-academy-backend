"""Abuse cases + signals (Phase B). Auto-block thresholds are simple stubs."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AbuseCase(Base):
    __tablename__ = "abuse_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    # open | investigating | mitigated | closed
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    summary: Mapped[str] = mapped_column(Text, default="")
    assigned_to: Mapped[str] = mapped_column(String(255), default="")
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AbuseSignal(Base):
    __tablename__ = "abuse_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("abuse_cases.id"), nullable=True, index=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    # e.g. sms_burst | payment_fraud | login_anomaly | api_abuse
    signal_type: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[str] = mapped_column(String(255), default="")  # user/ip/capability
    score: Mapped[int] = mapped_column(Integer, default=1)
    evidence: Mapped[str] = mapped_column(Text, default="")
    auto_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
