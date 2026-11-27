"""School fees tables. All tenant-scoped with RLS; ``tenant_id`` leads every key.

Amounts are BIGINT minor units. Check constraints repeat the domain's money rules so a bug in
application code can't write an impossible balance. Journal tables are append-only: the app role
may insert and read them, never update or delete.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.sqlalchemy.base import Base


def _student_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "student_id"],
        ["school_users.tenant_id", "school_users.id"],
        ondelete="RESTRICT",
    )


class FinanceSettingsRow(Base):
    __tablename__ = "finance_settings"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3))
    default_due_days: Mapped[int] = mapped_column(SmallInteger)
    online_payments_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    payment_proofs_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    payment_instructions: Mapped[str | None] = mapped_column(String(1000))
    bank_accounts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),)


class FinanceCounterRow(Base):
    __tablename__ = "finance_counters"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    series: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger)

    __table_args__ = (ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),)


class FeeItemRow(Base):
    __tablename__ = "finance_fee_items"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    school_level: Mapped[str | None] = mapped_column(String(16))
    class_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    terms: Mapped[list[str]] = mapped_column(ARRAY(String(16)), server_default=text("'{}'"))
    mandatory: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "class_id"],
            ["academic_classes.tenant_id", "academic_classes.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
    )


class ScholarshipRow(Base):
    __tablename__ = "finance_scholarships"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    session_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))
    value: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    granted_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _student_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["academic_sessions.tenant_id", "academic_sessions.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("value > 0", name="value_positive"),
        Index("ix_finance_scholarships_student", "tenant_id", "student_id", "session_id"),
        Index("ix_finance_scholarships_session", "tenant_id", "session_id"),
    )


class InvoiceRow(Base):
    __tablename__ = "finance_invoices"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    class_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    session_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    term_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    currency: Mapped[str] = mapped_column(String(3))
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    discounts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'"))
    subtotal_minor: Mapped[int] = mapped_column(BigInteger)
    total_minor: Mapped[int] = mapped_column(BigInteger)
    amount_paid_minor: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(16))
    due_on: Mapped[date] = mapped_column(Date)
    issued_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    voided_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))

    __table_args__ = (
        _student_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "term_id"],
            ["academic_terms.tenant_id", "academic_terms.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "number", name="uq_finance_invoices_number"),
        CheckConstraint(
            "total_minor >= 0 AND total_minor <= subtotal_minor", name="total_within_subtotal"
        ),
        CheckConstraint(
            "amount_paid_minor >= 0 AND amount_paid_minor <= total_minor", name="paid_within_total"
        ),
        CheckConstraint(
            "status IN ('issued', 'partially_paid', 'paid', 'void')", name="status_known"
        ),
        # A billing run can never bill the same student twice for a term.
        Index(
            "uq_finance_invoices_student_term",
            "tenant_id",
            "student_id",
            "term_id",
            unique=True,
            postgresql_where=text("kind = 'term' AND status <> 'void'"),
        ),
        Index("ix_finance_invoices_student_due", "tenant_id", "student_id", "due_on"),
        Index("ix_finance_invoices_term", "tenant_id", "term_id", "status"),
    )


class PaymentRow(Base):
    __tablename__ = "finance_payments"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    receipt_number: Mapped[str] = mapped_column(String(32))
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    method: Mapped[str] = mapped_column(String(16))
    reference: Mapped[str | None] = mapped_column(String(120))
    reference_key: Mapped[str | None] = mapped_column(String(120))
    received_on: Mapped[date] = mapped_column(Date)
    recorded_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    allocated_minor: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    refunded_minor: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    held_minor: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(16))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    reversed_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversal_reason: Mapped[str | None] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))

    __table_args__ = (
        _student_fk(),
        UniqueConstraint("tenant_id", "receipt_number", name="uq_finance_payments_receipt"),
        UniqueConstraint(
            "tenant_id", "recorded_by", "idempotency_key", name="uq_finance_payments_idempotency"
        ),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint(
            "allocated_minor >= 0 AND refunded_minor >= 0 AND held_minor >= 0 "
            "AND allocated_minor + refunded_minor + held_minor <= amount_minor",
            name="parts_within_amount",
        ),
        Index("ix_finance_payments_student", "tenant_id", "student_id", "received_on"),
        Index(
            "ix_finance_payments_reference",
            "tenant_id",
            "reference_key",
            postgresql_where=text("reference_key IS NOT NULL AND status = 'recorded'"),
        ),
    )


class AllocationRow(Base):
    __tablename__ = "finance_allocations"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    payment_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    invoice_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            ["finance_payments.tenant_id", "finance_payments.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            ["finance_invoices.tenant_id", "finance_invoices.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        Index("ix_finance_allocations_payment", "tenant_id", "payment_id"),
        Index("ix_finance_allocations_invoice", "tenant_id", "invoice_id"),
    )


class RefundRow(Base):
    __tablename__ = "finance_refunds"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    payment_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    method: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20))
    requested_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        _student_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            ["finance_payments.tenant_id", "finance_payments.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        # Dual control, enforced by the database as well as the domain.
        CheckConstraint(
            "decided_by IS NULL OR decided_by <> requested_by", name="different_approver"
        ),
        Index("ix_finance_refunds_status", "tenant_id", "status"),
        Index("ix_finance_refunds_student", "tenant_id", "student_id"),
    )


class JournalEntryRow(Base):
    __tablename__ = "finance_journal_entries"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    source_type: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    memo: Mapped[str] = mapped_column(String(200))
    posted_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        Index("ix_finance_journal_entries_source", "tenant_id", "source_type", "source_id"),
    )


class JournalLineRow(Base):
    __tablename__ = "finance_journal_lines"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    entry_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    line_no: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    account: Mapped[str] = mapped_column(String(32))
    student_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "entry_id"],
            ["finance_journal_entries.tenant_id", "finance_journal_entries.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("amount_minor <> 0", name="amount_nonzero"),
        Index(
            "ix_finance_journal_lines_student",
            "tenant_id",
            "student_id",
            "account",
            postgresql_where=text("student_id IS NOT NULL"),
        ),
        Index("ix_finance_journal_lines_account", "tenant_id", "account", "currency"),
    )


class PaymentProofRow(Base):
    __tablename__ = "finance_payment_proofs"

    tenant_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    student_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    submitted_by: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    method: Mapped[str] = mapped_column(String(16))
    paid_on: Mapped[date] = mapped_column(Date)
    reference: Mapped[str] = mapped_column(String(120))
    reference_key: Mapped[str] = mapped_column(String(120))
    payer_name: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(String(500))
    file_key: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(32))
    file_size: Mapped[int] = mapped_column(Integer)
    file_sha256: Mapped[str] = mapped_column(String(64))
    invoice_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16))
    reviewed_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(String(500))
    approved_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    payment_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))

    __table_args__ = (
        _student_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            ["finance_invoices.tenant_id", "finance_invoices.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            ["finance_payments.tenant_id", "finance_payments.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint(
            "status IN ('submitted', 'approved', 'rejected', 'withdrawn')", name="status_known"
        ),
        CheckConstraint(
            "reviewed_by IS NULL OR reviewed_by <> submitted_by", name="different_reviewer"
        ),
        CheckConstraint(
            "(status = 'approved') = (payment_id IS NOT NULL)", name="approved_has_payment"
        ),
        # The same bank reference or the same file can't be claimed twice while it is live.
        Index(
            "uq_finance_payment_proofs_reference",
            "tenant_id",
            "method",
            "reference_key",
            unique=True,
            postgresql_where=text("status IN ('submitted', 'approved')"),
        ),
        Index(
            "uq_finance_payment_proofs_file",
            "tenant_id",
            "file_sha256",
            unique=True,
            postgresql_where=text("status IN ('submitted', 'approved')"),
        ),
        Index("ix_finance_payment_proofs_queue", "tenant_id", "status", "submitted_at"),
        Index("ix_finance_payment_proofs_student", "tenant_id", "student_id"),
    )
