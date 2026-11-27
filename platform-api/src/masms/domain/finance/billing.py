"""Invoices and scholarships.

An invoice is issued once and never edited. Mistakes are fixed by voiding it (with a reason)
and issuing a new one, so every number a parent has seen stays explainable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from masms.domain.shared.entity import AggregateRoot, Entity
from masms.domain.shared.errors import InvalidStateTransition, ValidationFailed
from masms.domain.shared.events import DomainEvent
from masms.domain.shared.ids import new_id
from masms.domain.shared.money import BASIS_POINTS, Money, total

MAX_LINES = 50
MIN_REASON = 3
MAX_REASON = 500


def clean_reason(reason: str) -> str:
    clean = " ".join(reason.split())
    if not MIN_REASON <= len(clean) <= MAX_REASON:
        raise ValidationFailed("Give a reason.", details={"field": "reason"})
    return clean


def _text(value: str, field_name: str, max_len: int = 120) -> str:
    clean = " ".join(value.split())
    if not 1 <= len(clean) <= max_len:
        raise ValidationFailed(f"Enter up to {max_len} characters.", details={"field": field_name})
    return clean


class ScholarshipKind(StrEnum):
    PERCENT = "percent"  # value is basis points: 5 000 = 50 %
    FIXED = "fixed"  # value is minor units off each term invoice


@dataclass(kw_only=True, eq=False)
class Scholarship(Entity):
    """A discount for one student for one academic session, applied to each term invoice."""

    tenant_id: UUID
    student_id: UUID
    session_id: UUID
    name: str
    kind: ScholarshipKind
    value: int
    currency: str
    active: bool = True
    granted_by: UUID
    granted_at: datetime
    revoked_by: UUID | None = None
    revoked_at: datetime | None = None

    @classmethod
    def grant(
        cls,
        *,
        tenant_id: UUID,
        student_id: UUID,
        session_id: UUID,
        name: str,
        kind: ScholarshipKind,
        value: int,
        currency: str,
        by: UUID,
        now: datetime,
    ) -> Scholarship:
        if kind is ScholarshipKind.PERCENT and not 1 <= value <= BASIS_POINTS:
            raise ValidationFailed(
                "A percentage must be above 0 and at most 100.", details={"field": "value"}
            )
        if kind is ScholarshipKind.FIXED:
            Money.positive(value, currency, field="value")
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            student_id=student_id,
            session_id=session_id,
            name=_text(name, "name"),
            kind=kind,
            value=value,
            currency=currency,
            granted_by=by,
            granted_at=now,
        )

    def revoke(self, *, by: UUID, now: datetime) -> None:
        if not self.active:
            raise InvalidStateTransition("This scholarship is already revoked.")
        self.active = False
        self.revoked_by = by
        self.revoked_at = now

    def discount_on(self, subtotal: Money) -> Money:
        if self.kind is ScholarshipKind.PERCENT:
            return subtotal.share(self.value)
        return Money(self.value, self.currency).min(subtotal)


class InvoiceKind(StrEnum):
    TERM = "term"  # from a billing run; at most one live term invoice per student and term
    ADHOC = "adhoc"  # optional items, one-off charges


class InvoiceStatus(StrEnum):
    ISSUED = "issued"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    VOID = "void"


@dataclass(frozen=True, slots=True)
class InvoiceLine:
    description: str
    amount: Money
    fee_item_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class InvoiceDiscount:
    description: str
    amount: Money
    scholarship_id: UUID | None = None


@dataclass(frozen=True, kw_only=True)
class InvoiceIssued(DomainEvent):
    name: ClassVar[str] = "finance.invoice_issued"
    invoice_id: UUID
    student_id: UUID
    number: str
    total_minor: int
    currency: str
    due_on: str


@dataclass(kw_only=True, eq=False)
class Invoice(AggregateRoot):
    tenant_id: UUID
    number: str
    kind: InvoiceKind
    student_id: UUID
    class_id: UUID | None
    session_id: UUID | None
    term_id: UUID | None
    currency: str
    lines: list[InvoiceLine]
    discounts: list[InvoiceDiscount] = field(default_factory=list)
    status: InvoiceStatus = InvoiceStatus.ISSUED
    amount_paid: Money
    due_on: date
    issued_by: UUID
    issued_at: datetime
    voided_by: UUID | None = None
    voided_at: datetime | None = None
    void_reason: str | None = None
    version: int = 0

    @classmethod
    def issue(
        cls,
        *,
        tenant_id: UUID,
        number: str,
        kind: InvoiceKind,
        student_id: UUID,
        class_id: UUID | None,
        session_id: UUID | None,
        term_id: UUID | None,
        currency: str,
        lines: list[InvoiceLine],
        scholarships: list[Scholarship],
        due_on: date,
        today: date,
        by: UUID,
        now: datetime,
    ) -> Invoice:
        """``today`` is the school's local date, so due dates follow the school's calendar."""
        if not lines:
            raise ValidationFailed(
                "An invoice needs at least one item.", details={"field": "lines"}
            )
        if len(lines) > MAX_LINES:
            raise ValidationFailed("Too many items on one invoice.", details={"field": "lines"})
        if kind is InvoiceKind.TERM and term_id is None:
            raise ValidationFailed("A term invoice needs a term.", details={"field": "term_id"})
        if due_on < today:
            raise ValidationFailed(
                "The due date can't be in the past.", details={"field": "due_on"}
            )
        for line in lines:
            if line.amount.currency != currency or line.amount.amount_minor <= 0:
                raise ValidationFailed("Every item needs a positive amount.")
        subtotal = total([line.amount for line in lines], currency)
        discounts: list[InvoiceDiscount] = []
        remaining = subtotal
        for scholarship in scholarships:
            if not scholarship.active or remaining.is_zero:
                continue
            off = scholarship.discount_on(subtotal).min(remaining)
            if off.is_zero:
                continue
            discounts.append(
                InvoiceDiscount(
                    description=scholarship.name, amount=off, scholarship_id=scholarship.id
                )
            )
            remaining = remaining - off
        invoice = cls(
            id=new_id(),
            tenant_id=tenant_id,
            number=number,
            kind=kind,
            student_id=student_id,
            class_id=class_id,
            session_id=session_id,
            term_id=term_id,
            currency=currency,
            lines=list(lines),
            discounts=discounts,
            amount_paid=Money.zero(currency),
            due_on=due_on,
            issued_by=by,
            issued_at=now,
        )
        if invoice.total.is_zero:  # fully covered by scholarships: nothing to collect
            invoice.status = InvoiceStatus.PAID
        invoice.record(
            InvoiceIssued(
                tenant_id=tenant_id,
                invoice_id=invoice.id,
                student_id=student_id,
                number=number,
                total_minor=invoice.total.amount_minor,
                currency=currency,
                due_on=due_on.isoformat(),
            )
        )
        return invoice

    @property
    def subtotal(self) -> Money:
        return total([line.amount for line in self.lines], self.currency)

    @property
    def discount_total(self) -> Money:
        return total([d.amount for d in self.discounts], self.currency)

    @property
    def total(self) -> Money:
        return self.subtotal - self.discount_total

    @property
    def balance(self) -> Money:
        if self.status is InvoiceStatus.VOID:
            return Money.zero(self.currency)
        return self.total - self.amount_paid

    @property
    def is_open(self) -> bool:
        return self.status in {InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID}

    def _refresh_status(self) -> None:
        if self.amount_paid.is_zero:
            self.status = InvoiceStatus.ISSUED
        elif self.amount_paid >= self.total:
            self.status = InvoiceStatus.PAID
        else:
            self.status = InvoiceStatus.PARTIALLY_PAID

    def apply(self, amount: Money) -> None:
        if not self.is_open:
            raise InvalidStateTransition("This invoice is not open for payment.")
        if amount.amount_minor <= 0 or amount > self.balance:
            raise ValidationFailed("That amount is more than the invoice balance.")
        self.amount_paid = self.amount_paid + amount
        self._refresh_status()

    def release(self, amount: Money) -> None:
        """Take back part of what was applied (payment reversed or moved)."""
        if self.status is InvoiceStatus.VOID:
            raise InvalidStateTransition("This invoice is void.")
        if amount.amount_minor <= 0 or amount > self.amount_paid:
            raise ValidationFailed("That is more than was paid on this invoice.")
        self.amount_paid = self.amount_paid - amount
        self._refresh_status()

    def void(self, *, reason: str, by: UUID, now: datetime) -> Money:
        """Cancel the invoice. Returns what had been paid on it, which becomes student credit."""
        if self.status is InvoiceStatus.VOID:
            raise InvalidStateTransition("This invoice is already void.")
        released = self.amount_paid
        self.void_reason = clean_reason(reason)
        self.status = InvoiceStatus.VOID
        self.amount_paid = Money.zero(self.currency)
        self.voided_by = by
        self.voided_at = now
        return released
