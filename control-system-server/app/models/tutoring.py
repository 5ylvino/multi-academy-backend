"""Tutoring marketplace policy — platform-managed per tenant."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TutoringPolicy(Base):
    __tablename__ = "tutoring_policies"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_tutoring_policy_tenant"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    platform_fee_percent: Mapped[float] = mapped_column(Float, default=15.0)
    default_currency: Mapped[str] = mapped_column(String(8), default="NGN")
    allow_external_tutors: Mapped[bool] = mapped_column(Boolean, default=True)
    marketplace_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
