from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DealTemplate(Base):
    """Reusable deal blueprint — clone to tenant-specific custom subscriptions."""

    __tablename__ = "deal_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    price_minor: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    billing_cycle: Mapped[str] = mapped_column(String(32), default="monthly")
    # flat | per_student — per_student uses per_student_minor × student_count at invoice time
    pricing_model: Mapped[str] = mapped_column(String(32), default="flat")
    per_student_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    student_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entitlements: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quotas: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True)  # basic | premium | elite
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    monthly_price_minor: Mapped[int] = mapped_column(Integer, default=0)  # minor units
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    rank: Mapped[int] = mapped_column(Integer, default=0)  # basic=1, premium=2, elite=3
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    entitlements: Mapped[list["PlanEntitlement"]] = relationship(back_populates="plan")


class PlanEntitlement(Base):
    """feature_key enabled (and optional quota) for a catalog plan."""

    __tablename__ = "entitlements"
    __table_args__ = (UniqueConstraint("plan_id", "feature_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), index=True)
    feature_key: Mapped[str] = mapped_column(String(64), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    quota: Mapped[int | None] = mapped_column(Integer, nullable=True)  # e.g. sms_monthly

    plan: Mapped[Plan] = relationship(back_populates="entitlements")


class Subscription(Base):
    """Tenant ↔ catalog plan OR custom deal."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    type: Mapped[str] = mapped_column(String(16), default="catalog")  # catalog | custom
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plans.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    # active | trialing | past_due | cancelled | cancelled_blacklisted
    billing_cycle: Mapped[str] = mapped_column(String(32), default="monthly")
    price_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)  # flat / period price
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    # Pricing: flat (fixed period price) | per_student (per_student_minor × student_count)
    pricing_model: Mapped[str] = mapped_column(String(32), default="flat")
    per_student_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    student_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Custom deal levers: cherry-picked entitlements {feature_key: bool}, quotas, contract terms.
    custom_entitlements: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    custom_quotas: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    contract_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contract_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    # Dunning (Phase B): none | past_due | grace | suspended
    dunning_step: Mapped[str] = mapped_column(String(32), default="none")
    past_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Human-readable deal/offer name for custom subscriptions
    deal_name: Mapped[str] = mapped_column(String(255), default="")
    # Custom deals: draft | pending | approved | active (catalog subs stay active)
    approval_status: Mapped[str] = mapped_column(String(32), default="active")
    next_invoice_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Optional termly billing schedule. Dates are ISO-8601 strings in order.
    billing_schedule: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
