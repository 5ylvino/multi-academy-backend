"""Payroll tables."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.sqlalchemy.base import Base


class PayrollSettingsRow(Base):
    __tablename__ = "payroll_settings"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3))
    tax_bands: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    tax_free_monthly_minor: Mapped[int] = mapped_column(BigInteger)
    pension_employee_bp: Mapped[int] = mapped_column(Integer)
    pension_employer_bp: Mapped[int] = mapped_column(Integer)
    housing_fund_bp: Mapped[int] = mapped_column(Integer)
    rent_relief_bp: Mapped[int] = mapped_column(Integer)
    rent_relief_cap_minor: Mapped[int] = mapped_column(BigInteger)
    pay_day: Mapped[int] = mapped_column(SmallInteger)
    updated_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        CheckConstraint(
            "pension_employee_bp BETWEEN 0 AND 10000 AND pension_employer_bp BETWEEN 0 AND 10000"
            " AND housing_fund_bp BETWEEN 0 AND 10000 AND rent_relief_bp BETWEEN 0 AND 10000",
            name="rates_in_range",
        ),
        CheckConstraint("pay_day BETWEEN 1 AND 31", name="pay_day_in_range"),
    )


class PayProfileRow(Base):
    __tablename__ = "payroll_profiles"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    staff_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    basic_minor: Mapped[int] = mapped_column(BigInteger)
    allowances: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    deductions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    annual_rent_minor: Mapped[int] = mapped_column(BigInteger)
    bank_name: Mapped[str | None] = mapped_column(String(80))
    account_number_ciphertext: Mapped[str | None] = mapped_column(String(255))
    account_hint: Mapped[str | None] = mapped_column(String(12))
    tax_id: Mapped[str | None] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    updated_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "staff_id"],
            ["school_users.tenant_id", "school_users.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("basic_minor > 0", name="basic_positive"),
        CheckConstraint("annual_rent_minor >= 0", name="rent_not_negative"),
        Index("ix_payroll_profiles_active", "tenant_id", "active"),
    )


class PayRunRow(Base):
    __tablename__ = "payroll_runs"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger)
    month: Mapped[int] = mapped_column(SmallInteger)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(10))
    staff_count: Mapped[int] = mapped_column(Integer)
    gross_minor: Mapped[int] = mapped_column(BigInteger)
    net_minor: Mapped[int] = mapped_column(BigInteger)
    paye_minor: Mapped[int] = mapped_column(BigInteger)
    pension_employee_minor: Mapped[int] = mapped_column(BigInteger)
    pension_employer_minor: Mapped[int] = mapped_column(BigInteger)
    housing_fund_minor: Mapped[int] = mapped_column(BigInteger)
    other_deductions_minor: Mapped[int] = mapped_column(BigInteger)
    prepared_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    prepared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_reference: Mapped[str | None] = mapped_column(String(120))
    void_reason: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        CheckConstraint("status IN ('draft', 'approved', 'paid', 'void')", name="status_known"),
        CheckConstraint("month BETWEEN 1 AND 12", name="month_in_range"),
        CheckConstraint(
            "approved_by IS NULL OR approved_by <> prepared_by", name="two_person_approval"
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'paid') OR approved_by IS NOT NULL",
            name="approved_has_approver",
        ),
        CheckConstraint(
            "status <> 'paid' OR (paid_by IS NOT NULL AND payment_reference IS NOT NULL)",
            name="paid_has_reference",
        ),
        CheckConstraint(
            "gross_minor = net_minor + paye_minor + pension_employee_minor"
            " + housing_fund_minor + other_deductions_minor",
            name="totals_balance",
        ),
        Index(
            "uq_payroll_runs_month",
            "tenant_id",
            "year",
            "month",
            unique=True,
            postgresql_where=text("status <> 'void'"),
        ),
        Index("ix_payroll_runs_period", "tenant_id", "year", "month"),
    )


class PayslipRow(Base):
    """Frozen once the run leaves draft: a trigger refuses changes after that."""

    __tablename__ = "payroll_payslips"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    staff_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    basic_minor: Mapped[int] = mapped_column(BigInteger)
    allowances: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    gross_minor: Mapped[int] = mapped_column(BigInteger)
    pension_employee_minor: Mapped[int] = mapped_column(BigInteger)
    pension_employer_minor: Mapped[int] = mapped_column(BigInteger)
    housing_fund_minor: Mapped[int] = mapped_column(BigInteger)
    taxable_annual_minor: Mapped[int] = mapped_column(BigInteger)
    paye_minor: Mapped[int] = mapped_column(BigInteger)
    other_deductions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    net_minor: Mapped[int] = mapped_column(BigInteger)
    bank_name: Mapped[str | None] = mapped_column(String(80))
    account_hint: Mapped[str | None] = mapped_column(String(12))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["payroll_runs.tenant_id", "payroll_runs.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "staff_id"],
            ["school_users.tenant_id", "school_users.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("net_minor >= 0", name="net_not_negative"),
        CheckConstraint(
            "gross_minor >= basic_minor AND basic_minor > 0", name="gross_covers_basic"
        ),
        Index("ix_payroll_payslips_staff", "tenant_id", "staff_id"),
    )
