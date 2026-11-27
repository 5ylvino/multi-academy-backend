"""Payments, how they settle invoices, and refunds.

Every payment first becomes credit on the student's account. Credit then settles open invoices,
oldest due date first. Whatever is left stays as credit for the next invoice or a refund.
Refunds need two people: one asks, a different one approves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from masms.domain.finance.billing import Invoice, clean_reason
from masms.domain.shared.entity import AggregateRoot, Entity
from masms.domain.shared.errors import (
    InvalidStateTransition,
    PermissionDenied,
    ValidationFailed,
)
from masms.domain.shared.events import DomainEvent
from masms.domain.shared.ids import new_id
from masms.domain.shared.money import Money

MAX_REFERENCE = 120


class PaymentMethod(StrEnum):
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    POS = "pos"
    CHEQUE = "cheque"
    ONLINE = "online"  # recorded by the gateway integration, never by hand


MANUAL_METHODS = frozenset(
    {PaymentMethod.CASH, PaymentMethod.BANK_TRANSFER, PaymentMethod.POS, PaymentMethod.CHEQUE}
)


def parse_method(value: str, *, manual: bool = True) -> PaymentMethod:
    try:
        method = PaymentMethod(value)
    except ValueError as exc:
        raise ValidationFailed("Unknown payment method.", details={"field": "method"}) from exc
    if manual and method not in MANUAL_METHODS:
        raise ValidationFailed(
            "Online payments are recorded by the payment gateway.", details={"field": "method"}
        )
    return method


class PaymentStatus(StrEnum):
    RECORDED = "recorded"
    REVERSED = "reversed"


@dataclass(frozen=True, kw_only=True)
class PaymentRecorded(DomainEvent):
    name: ClassVar[str] = "finance.payment_recorded"
    payment_id: UUID
    student_id: UUID
    receipt_number: str
    amount_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class Allocation:
    payment_id: UUID
    invoice_id: UUID
    amount: Money


@dataclass(kw_only=True, eq=False)
class Payment(AggregateRoot):
    tenant_id: UUID
    receipt_number: str
    student_id: UUID
    amount: Money
    method: PaymentMethod
    reference: str | None
    received_on: date
    recorded_by: UUID
    recorded_at: datetime
    allocated: Money
    refunded: Money
    held: Money  # reserved by refunds waiting for approval
    status: PaymentStatus = PaymentStatus.RECORDED
    idempotency_key: str | None = None
    request_fingerprint: str | None = None
    reversed_by: UUID | None = None
    reversed_at: datetime | None = None
    reversal_reason: str | None = None
    version: int = 0

    @classmethod
    def receive(
        cls,
        *,
        tenant_id: UUID,
        receipt_number: str,
        student_id: UUID,
        amount: Money,
        method: PaymentMethod,
        reference: str | None,
        received_on: date,
        by: UUID,
        now: datetime,
        idempotency_key: str | None = None,
        request_fingerprint: str | None = None,
    ) -> Payment:
        if amount.amount_minor <= 0:
            raise ValidationFailed(
                "Enter an amount greater than zero.", details={"field": "amount"}
            )
        if received_on > now.date():
            raise ValidationFailed(
                "The payment date can't be in the future.", details={"field": "received_on"}
            )
        ref = " ".join((reference or "").split()) or None
        if ref is not None and len(ref) > MAX_REFERENCE:
            raise ValidationFailed("The reference is too long.", details={"field": "reference"})
        if method in {PaymentMethod.BANK_TRANSFER, PaymentMethod.CHEQUE} and ref is None:
            raise ValidationFailed(
                "Enter the transfer or cheque reference.", details={"field": "reference"}
            )
        zero = Money.zero(amount.currency)
        payment = cls(
            id=new_id(),
            tenant_id=tenant_id,
            receipt_number=receipt_number,
            student_id=student_id,
            amount=amount,
            method=method,
            reference=ref,
            received_on=received_on,
            recorded_by=by,
            recorded_at=now,
            allocated=zero,
            refunded=zero,
            held=zero,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        payment.record(
            PaymentRecorded(
                tenant_id=tenant_id,
                payment_id=payment.id,
                student_id=student_id,
                receipt_number=receipt_number,
                amount_minor=amount.amount_minor,
                currency=amount.currency,
            )
        )
        return payment

    @property
    def available(self) -> Money:
        """Credit left on this payment that can settle invoices or be refunded."""
        if self.status is PaymentStatus.REVERSED:
            return Money.zero(self.amount.currency)
        return self.amount - self.allocated - self.refunded - self.held

    def allocate(self, amount: Money) -> None:
        if amount.amount_minor <= 0 or amount > self.available:
            raise ValidationFailed("Not enough credit on this payment.")
        self.allocated = self.allocated + amount

    def deallocate(self, amount: Money) -> None:
        if amount > self.allocated:
            raise ValidationFailed("That is more than this payment settled.")
        self.allocated = self.allocated - amount

    def hold(self, amount: Money) -> None:
        if amount.amount_minor <= 0 or amount > self.available:
            raise ValidationFailed(
                "The refund is more than the unused credit on this payment.",
                code="insufficient_credit",
                details={"field": "amount"},
            )
        self.held = self.held + amount

    def release_hold(self, amount: Money) -> None:
        self.held = self.held - amount

    def settle_refund(self, amount: Money) -> None:
        self.held = self.held - amount
        self.refunded = self.refunded + amount

    def reverse(self, *, reason: str, by: UUID, now: datetime) -> None:
        """Undo a payment that never really arrived (bounced cheque, keyed in twice)."""
        if self.status is PaymentStatus.REVERSED:
            raise InvalidStateTransition("This payment is already reversed.")
        if not self.refunded.is_zero or not self.held.is_zero:
            raise InvalidStateTransition(
                "This payment has refunds against it and can't be reversed."
            )
        if self.allocated.amount_minor != 0:
            raise InvalidStateTransition("Release the invoices this payment settled first.")
        self.reversal_reason = clean_reason(reason)
        self.status = PaymentStatus.REVERSED
        self.reversed_by = by
        self.reversed_at = now


def plan_settlement(
    credits: list[Payment], invoices: list[Invoice]
) -> list[tuple[Payment, Invoice, Money]]:
    """Pair available credit with open invoices: oldest payments first, oldest due first.

    Pure: callers apply the result. Same currency is assumed (one currency per school).
    """
    open_invoices = sorted(
        (i for i in invoices if i.is_open and i.balance.amount_minor > 0),
        key=lambda i: (i.due_on, i.issued_at, str(i.id)),
    )
    sources = sorted(
        (p for p in credits if p.available.amount_minor > 0),
        key=lambda p: (p.received_on, p.recorded_at, str(p.id)),
    )
    plan: list[tuple[Payment, Invoice, Money]] = []
    left = {p.id: p.available for p in sources}
    owed = {i.id: i.balance for i in open_invoices}
    for invoice in open_invoices:
        for payment in sources:
            if owed[invoice.id].is_zero:
                break
            if left[payment.id].is_zero:
                continue
            amount = left[payment.id].min(owed[invoice.id])
            plan.append((payment, invoice, amount))
            left[payment.id] = left[payment.id] - amount
            owed[invoice.id] = owed[invoice.id] - amount
    return plan


class RefundStatus(StrEnum):
    PENDING = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(kw_only=True, eq=False)
class Refund(Entity):
    tenant_id: UUID
    payment_id: UUID
    student_id: UUID
    amount: Money
    method: PaymentMethod
    reason: str
    status: RefundStatus = RefundStatus.PENDING
    requested_by: UUID
    requested_at: datetime
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None

    @classmethod
    def request(
        cls,
        *,
        payment: Payment,
        amount: Money,
        method: PaymentMethod,
        reason: str,
        by: UUID,
        now: datetime,
    ) -> Refund:
        if method is PaymentMethod.ONLINE:
            raise ValidationFailed(
                "Online refunds go back through the payment gateway.", details={"field": "method"}
            )
        payment.hold(amount)
        return cls(
            id=new_id(),
            tenant_id=payment.tenant_id,
            payment_id=payment.id,
            student_id=payment.student_id,
            amount=amount,
            method=method,
            reason=clean_reason(reason),
            requested_by=by,
            requested_at=now,
        )

    def _decide(self, by: UUID, now: datetime, note: str | None) -> None:
        if self.status is not RefundStatus.PENDING:
            raise InvalidStateTransition("This refund has already been decided.")
        if by == self.requested_by:
            raise PermissionDenied(
                "A different person must approve or reject a refund you requested.",
                code="dual_control_required",
            )
        self.decided_by = by
        self.decided_at = now
        self.decision_note = " ".join((note or "").split())[:500] or None

    def approve(self, payment: Payment, *, by: UUID, now: datetime, note: str | None) -> None:
        self._decide(by, now, note)
        payment.settle_refund(self.amount)
        self.status = RefundStatus.APPROVED

    def reject(self, payment: Payment, *, by: UUID, now: datetime, note: str | None) -> None:
        self._decide(by, now, note)
        payment.release_hold(self.amount)
        self.status = RefundStatus.REJECTED
