"""Provider-neutral financial routing contracts.

These rows contain routing metadata only. Provider credentials remain in the
provider secret store and KIRA is intentionally not a supported adapter.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FeeSplitRule(Base):
    __tablename__ = "fee_split_rules"
    __table_args__ = (UniqueConstraint("tenant_id", "fee_type", name="uq_fee_split_tenant_type"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    fee_type: Mapped[str] = mapped_column(String(64), default="school_fees")
    provider_id: Mapped[str] = mapped_column(String(64), default="")
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    allocations: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PayrollAccount(Base):
    __tablename__ = "payroll_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "account_role", name="uq_payroll_tenant_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    account_role: Mapped[str] = mapped_column(String(32), default="salary")  # salary | operating
    provider_id: Mapped[str] = mapped_column(String(64), default="")
    account_reference: Mapped[str] = mapped_column(String(128), default="")
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PayrollRun(Base):
    """Approval-controlled payroll payout intent; execution stays provider-neutral."""

    __tablename__ = "payroll_runs"
    __table_args__ = (UniqueConstraint("tenant_id", "period_key", name="uq_payroll_tenant_period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    period_key: Mapped[str] = mapped_column(String(32))
    provider_id: Mapped[str] = mapped_column(String(64), default="")
    salary_account_id: Mapped[int] = mapped_column(ForeignKey("payroll_accounts.id"))
    gross_minor: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    status: Mapped[str] = mapped_column(String(24), default="draft")  # draft | pending_approval | approved | submitted | paid | failed
    created_by: Mapped[str] = mapped_column(String(255), default="")
    approved_by: Mapped[str] = mapped_column(String(255), default="")
    external_reference: Mapped[str] = mapped_column(String(128), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
