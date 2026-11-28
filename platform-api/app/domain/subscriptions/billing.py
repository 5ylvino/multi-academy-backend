"""Platform billing: a school's subscription and the invoices the platform sends it.

Lifecycle:

- A school starts on a free trial. Its subscription's first period begins when the trial ends.
- ``ISSUE_AHEAD`` before a period begins, the platform issues an invoice for it, due on the day
  the period begins. Price = plan base fee + per-student fee x active students on that day.
- Paid: the period starts (or the trial converts) and the school is active.
- Unpaid past the due date: the subscription is ``past_due`` and the school is reminded, but
  keeps working through ``GRACE_PERIOD``. After that the school is suspended for non-payment;
  paying reinstates it at once. Suspensions for other reasons are never lifted by a payment.
- Free plans roll from period to period without invoices.
"""

from __future__ import annotations

import calendar
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from app.domain.shared.entity import AggregateRoot
from app.domain.shared.errors import InvalidStateTransition, ValidationFailed
from app.domain.shared.events import DomainEvent
from app.domain.shared.ids import new_id
from app.domain.shared.money import Money

ISSUE_AHEAD = timedelta(days=7)
GRACE_PERIOD = timedelta(days=14)
REMIND_EVERY = timedelta(days=3)
#: Reminders stop this long after the due date; by then the school is suspended and knows.
REMIND_WINDOW = timedelta(days=30)
#: Status reason on a suspension made by billing; only these are lifted by a payment.
NON_PAYMENT_PREFIX = "Unpaid platform invoice"
_REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def add_months(moment: datetime, months: int) -> datetime:
    """Same day and time ``months`` later, clamped to the month's last day (31 Jan + 1 = 28 Feb)."""
    index = moment.month - 1 + months
    year, month = moment.year + index // 12, index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def invoice_number(sequence: int, year: int) -> str:
    return f"MAS-{year}-{sequence:06d}"


def new_checkout_reference() -> str:
    return "MASP-" + "".join(secrets.choice(_REFERENCE_ALPHABET) for _ in range(20))


class SubscriptionStatus(StrEnum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"


@dataclass(kw_only=True, eq=False)
class Subscription(AggregateRoot):
    """One per school; ``id`` is the school's id. ``current_period_end`` is when the next
    period (and its invoice) begins."""

    plan_code: str
    status: SubscriptionStatus
    current_period_start: datetime
    current_period_end: datetime
    created_at: datetime
    updated_at: datetime

    @property
    def tenant_id(self) -> UUID:
        return self.id

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        plan_code: str,
        trialing: bool,
        started: datetime,
        first_bill_at: datetime,
        now: datetime,
    ) -> Subscription:
        """``first_bill_at`` is when the first paid period begins (the trial's end, for a new
        school)."""
        return cls(
            id=tenant_id,
            plan_code=plan_code,
            status=SubscriptionStatus.TRIALING if trialing else SubscriptionStatus.ACTIVE,
            current_period_start=min(started, first_bill_at),
            current_period_end=first_bill_at,
            created_at=now,
            updated_at=now,
        )

    def begin_period(self, start: datetime, end: datetime, now: datetime) -> None:
        if self.status is SubscriptionStatus.CANCELLED:
            raise InvalidStateTransition("This subscription is cancelled.")
        if end > self.current_period_end:
            self.current_period_start, self.current_period_end = start, end
        self.status = SubscriptionStatus.ACTIVE
        self.updated_at = now

    def mark_past_due(self, now: datetime) -> bool:
        if self.status in {SubscriptionStatus.TRIALING, SubscriptionStatus.ACTIVE}:
            self.status = SubscriptionStatus.PAST_DUE
            self.updated_at = now
            return True
        return False

    def change_plan(self, plan_code: str, now: datetime) -> None:
        self.plan_code = plan_code
        self.updated_at = now


class InvoiceStatus(StrEnum):
    OPEN = "open"
    PAID = "paid"
    VOID = "void"


class SettlementMethod(StrEnum):
    ONLINE = "online"
    BANK_TRANSFER = "bank_transfer"
    WAIVED = "waived"


@dataclass(frozen=True, kw_only=True)
class PlatformInvoiceIssued(DomainEvent):
    name: ClassVar[str] = "subscriptions.invoice_issued"
    invoice_id: UUID
    number: str
    total_minor: int
    currency: str
    due_at: datetime
    period_start: datetime
    period_end: datetime


@dataclass(frozen=True, kw_only=True)
class PlatformInvoiceOverdue(DomainEvent):
    name: ClassVar[str] = "subscriptions.invoice_overdue"
    invoice_id: UUID
    number: str
    total_minor: int
    currency: str
    due_at: datetime
    suspends_at: datetime


@dataclass(frozen=True, kw_only=True)
class PlatformInvoicePaid(DomainEvent):
    name: ClassVar[str] = "subscriptions.invoice_paid"
    invoice_id: UUID
    number: str
    total_minor: int
    currency: str


@dataclass(kw_only=True, eq=False)
class PlatformInvoice(AggregateRoot):
    tenant_id: UUID
    number: str
    plan_code: str
    period_start: datetime
    period_end: datetime
    student_count: int
    base: Money
    per_student: Money
    total: Money
    issued_at: datetime
    due_at: datetime
    status: InvoiceStatus = InvoiceStatus.OPEN
    paid_at: datetime | None = None
    settlement_method: SettlementMethod | None = None
    settlement_reference: str | None = None
    settled_by: UUID | None = None
    note: str | None = None
    checkout_reference: str | None = None
    authorization_url: str | None = None
    checkout_started_at: datetime | None = None
    reminded_at: datetime | None = None

    @classmethod
    def issue(
        cls,
        *,
        tenant_id: UUID,
        number: str,
        plan_code: str,
        currency: str,
        base_minor: int,
        per_student_minor: int,
        student_count: int,
        period_start: datetime,
        period_end: datetime,
        now: datetime,
    ) -> PlatformInvoice:
        if period_end <= period_start:
            raise ValidationFailed("A billing period must end after it starts.")
        base = Money(base_minor, currency)
        per_student = Money(per_student_minor, currency)
        total = Money(base_minor + per_student_minor * max(student_count, 0), currency)
        invoice = cls(
            id=new_id(),
            tenant_id=tenant_id,
            number=number,
            plan_code=plan_code,
            period_start=period_start,
            period_end=period_end,
            student_count=max(student_count, 0),
            base=base,
            per_student=per_student,
            total=total,
            issued_at=now,
            # Never due before it was issued, even when issued late.
            due_at=max(period_start, now),
        )
        invoice.record(
            PlatformInvoiceIssued(
                tenant_id=tenant_id,
                invoice_id=invoice.id,
                number=number,
                total_minor=total.amount_minor,
                currency=currency,
                due_at=invoice.due_at,
                period_start=period_start,
                period_end=period_end,
            )
        )
        return invoice

    @property
    def suspends_at(self) -> datetime:
        return self.due_at + GRACE_PERIOD

    def is_overdue(self, now: datetime) -> bool:
        return self.status is InvoiceStatus.OPEN and now > self.due_at

    def should_remind(self, now: datetime) -> bool:
        return (
            self.is_overdue(now)
            and now - self.due_at <= REMIND_WINDOW
            and (self.reminded_at is None or now - self.reminded_at >= REMIND_EVERY)
        )

    def remind(self, now: datetime) -> None:
        self.reminded_at = now
        self.record(
            PlatformInvoiceOverdue(
                tenant_id=self.tenant_id,
                invoice_id=self.id,
                number=self.number,
                total_minor=self.total.amount_minor,
                currency=self.total.currency,
                due_at=self.due_at,
                suspends_at=self.suspends_at,
            )
        )

    def start_checkout(self, reference: str, url: str, now: datetime) -> None:
        self._ensure_open()
        self.checkout_reference = reference
        self.authorization_url = url
        self.checkout_started_at = now

    def settle(
        self,
        *,
        method: SettlementMethod,
        reference: str | None,
        by: UUID | None,
        now: datetime,
        note: str | None = None,
    ) -> bool:
        """Returns False when already paid (repeat confirmations are harmless)."""
        if self.status is InvoiceStatus.PAID:
            return False
        self._ensure_open()
        if method is SettlementMethod.WAIVED and not (note and note.strip()):
            raise ValidationFailed("Say why the invoice is waived.", details={"field": "note"})
        self.status = InvoiceStatus.PAID
        self.paid_at = now
        self.settlement_method = method
        self.settlement_reference = (reference or "").strip()[:120] or None
        self.settled_by = by
        self.note = (note or "").strip()[:500] or None
        self.authorization_url = None
        self.record(
            PlatformInvoicePaid(
                tenant_id=self.tenant_id,
                invoice_id=self.id,
                number=self.number,
                total_minor=self.total.amount_minor,
                currency=self.total.currency,
            )
        )
        return True

    def void(self, reason: str, *, by: UUID, now: datetime) -> None:
        self._ensure_open()
        if not reason.strip():
            raise ValidationFailed("Say why the invoice is cancelled.", details={"field": "reason"})
        self.status = InvoiceStatus.VOID
        self.note = reason.strip()[:500]
        self.settled_by = by
        self.paid_at = None
        self.authorization_url = None
        _ = now

    def _ensure_open(self) -> None:
        if self.status is not InvoiceStatus.OPEN:
            raise InvalidStateTransition(
                f"This invoice is already {self.status.value}.", code="invoice_closed"
            )
