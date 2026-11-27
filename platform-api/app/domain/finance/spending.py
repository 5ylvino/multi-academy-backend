"""School spending: expense categories, a budget per category per term, and expenses.

An expense is recorded by one person and approved by another before it counts. Approval posts
it to the ledger (debit expenses, credit the cash or bank account it was paid from); voiding an
approved expense posts the opposite entry. Approving an expense that takes a category over its
budget needs a note saying why, so overspending is always a deliberate, recorded choice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from app.domain.finance.payments import MANUAL_METHODS, PaymentMethod
from app.domain.finance.proofs import ProofFile
from app.domain.shared.entity import AggregateRoot
from app.domain.shared.errors import (
    InvalidStateTransition,
    PermissionDenied,
    ValidationFailed,
)
from app.domain.shared.events import DomainEvent
from app.domain.shared.ids import new_id
from app.domain.shared.money import Money

EXPENSE_SERIES = "expense"
#: Suggested categories for a new school; schools rename, add and retire their own.
DEFAULT_CATEGORIES = (
    "Teaching materials",
    "Stationery and printing",
    "Utilities",
    "Repairs and maintenance",
    "Cleaning and sanitation",
    "Transport and fuel",
    "Feeding",
    "ICT and software",
    "Security",
    "Events and sports",
    "Staff welfare and training",
    "Other",
)
MAX_SPENT_DAYS_AGO = 366


def expense_number(value: int, year: int) -> str:
    return f"EXP-{year}-{value:06d}"


def _clean(text: str | None, limit: int) -> str | None:
    value = " ".join((text or "").split())
    return value[:limit] or None


def category_name(name: str) -> str:
    clean = _clean(name, 80)
    if clean is None or len(clean) < 2:
        raise ValidationFailed("Category name must be 2-80 characters.", details={"field": "name"})
    return clean


@dataclass(kw_only=True, eq=False)
class ExpenseCategory:
    id: UUID
    tenant_id: UUID
    name: str
    active: bool
    created_at: datetime

    @classmethod
    def create(cls, *, tenant_id: UUID, name: str, now: datetime) -> ExpenseCategory:
        return cls(
            id=new_id(), tenant_id=tenant_id, name=category_name(name), active=True, created_at=now
        )


@dataclass(kw_only=True, eq=False)
class Budget:
    """What the school plans to spend on one category in one term."""

    id: UUID
    tenant_id: UUID
    term_id: UUID
    category_id: UUID
    planned: Money
    updated_by: UUID
    updated_at: datetime

    @classmethod
    def plan(
        cls,
        *,
        tenant_id: UUID,
        term_id: UUID,
        category_id: UUID,
        planned: Money,
        by: UUID,
        now: datetime,
    ) -> Budget:
        _check_planned(planned)
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            term_id=term_id,
            category_id=category_id,
            planned=planned,
            updated_by=by,
            updated_at=now,
        )

    def replan(self, planned: Money, *, by: UUID, now: datetime) -> None:
        _check_planned(planned)
        if planned.currency != self.planned.currency:
            raise ValidationFailed("Keep the budget's currency.", details={"field": "currency"})
        self.planned = planned
        self.updated_by = by
        self.updated_at = now


def _check_planned(planned: Money) -> None:
    if planned.amount_minor < 0:
        raise ValidationFailed("A budget can't be negative.", details={"field": "planned_minor"})


class ExpenseStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    VOID = "void"


@dataclass(frozen=True, kw_only=True)
class ExpenseSubmitted(DomainEvent):
    name: ClassVar[str] = "finance.expense_submitted"
    expense_id: UUID
    number: str
    amount_minor: int
    currency: str


@dataclass(kw_only=True, eq=False)
class Expense(AggregateRoot):
    tenant_id: UUID
    number: str
    category_id: UUID
    term_id: UUID
    amount: Money
    payee: str
    description: str
    spent_on: date
    method: PaymentMethod
    reference: str | None
    recorded_by: UUID
    recorded_at: datetime
    status: ExpenseStatus = ExpenseStatus.PENDING
    receipt: ProofFile | None = None
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    over_budget_note: str | None = None
    voided_by: UUID | None = None
    voided_at: datetime | None = None
    void_reason: str | None = None

    @classmethod
    def submit(
        cls,
        *,
        tenant_id: UUID,
        number: str,
        category_id: UUID,
        term_id: UUID,
        amount: Money,
        payee: str,
        description: str,
        spent_on: date,
        method: PaymentMethod,
        reference: str | None,
        by: UUID,
        today: date,
        now: datetime,
    ) -> Expense:
        if amount.amount_minor <= 0:
            raise ValidationFailed("Enter an amount above zero.", details={"field": "amount_minor"})
        if method not in MANUAL_METHODS:
            raise ValidationFailed("Pick how it was paid.", details={"field": "method"})
        if spent_on > today:
            raise ValidationFailed(
                "The date can't be in the future.", details={"field": "spent_on"}
            )
        if (today - spent_on).days > MAX_SPENT_DAYS_AGO:
            raise ValidationFailed(
                "That date is more than a year ago.", details={"field": "spent_on"}
            )
        clean_payee = _clean(payee, 160)
        if clean_payee is None:
            raise ValidationFailed("Say who was paid.", details={"field": "payee"})
        clean_description = _clean(description, 500)
        if clean_description is None or len(clean_description) < 3:
            raise ValidationFailed("Say what it was for.", details={"field": "description"})
        if method is not PaymentMethod.CASH and not _clean(reference, 120):
            raise ValidationFailed(
                "Enter the transfer, POS or cheque reference.", details={"field": "reference"}
            )
        expense = cls(
            id=new_id(),
            tenant_id=tenant_id,
            number=number,
            category_id=category_id,
            term_id=term_id,
            amount=amount,
            payee=clean_payee,
            description=clean_description,
            spent_on=spent_on,
            method=method,
            reference=_clean(reference, 120),
            recorded_by=by,
            recorded_at=now,
        )
        expense.record(
            ExpenseSubmitted(
                tenant_id=tenant_id,
                expense_id=expense.id,
                number=number,
                amount_minor=amount.amount_minor,
                currency=amount.currency,
            )
        )
        return expense

    def attach_receipt(self, receipt: ProofFile) -> None:
        """A receipt can be added (or replaced) until the expense is closed."""
        if self.status in {ExpenseStatus.REJECTED, ExpenseStatus.VOID}:
            raise InvalidStateTransition(
                f"This expense is {self.status.value}.", code="expense_closed"
            )
        self.receipt = receipt

    def approve(
        self, *, by: UUID, now: datetime, note: str | None, over_budget_note: str | None
    ) -> None:
        self._ensure(ExpenseStatus.PENDING)
        if by == self.recorded_by:
            raise PermissionDenied(
                "Someone else must approve an expense you recorded.", code="same_person"
            )
        self.status = ExpenseStatus.APPROVED
        self.decided_by = by
        self.decided_at = now
        self.decision_note = _clean(note, 500)
        self.over_budget_note = _clean(over_budget_note, 500)

    def reject(self, *, by: UUID, now: datetime, reason: str) -> None:
        self._ensure(ExpenseStatus.PENDING)
        if by == self.recorded_by:
            raise PermissionDenied(
                "Withdraw it instead; someone else reviews what you recorded.", code="same_person"
            )
        clean = _clean(reason, 500)
        if clean is None:
            raise ValidationFailed("Say why it is rejected.", details={"field": "reason"})
        self.status = ExpenseStatus.REJECTED
        self.decided_by = by
        self.decided_at = now
        self.decision_note = clean

    def void(self, *, by: UUID, now: datetime, reason: str) -> bool:
        """Cancel a pending expense, or reverse an approved one. Returns True when the ledger
        needs a reversing entry."""
        if self.status not in {ExpenseStatus.PENDING, ExpenseStatus.APPROVED}:
            raise InvalidStateTransition(
                f"This expense is already {self.status.value}.", code="expense_closed"
            )
        clean = _clean(reason, 500)
        if clean is None:
            raise ValidationFailed("Say why it is cancelled.", details={"field": "reason"})
        was_approved = self.status is ExpenseStatus.APPROVED
        self.status = ExpenseStatus.VOID
        self.voided_by = by
        self.voided_at = now
        self.void_reason = clean
        return was_approved

    def _ensure(self, status: ExpenseStatus) -> None:
        if self.status is not status:
            raise InvalidStateTransition(
                f"This expense is already {self.status.value}.", code="expense_closed"
            )

    @staticmethod
    def receipt_key(tenant_id: UUID, expense_id: UUID, extension: str) -> str:
        return f"tenants/{tenant_id}/expenses/{expense_id}/receipt.{extension}"


@dataclass(frozen=True, slots=True)
class CategorySpend:
    """Budget against actual for one category in one term."""

    category_id: UUID
    planned: Money
    approved: Money
    pending: Money

    @property
    def remaining(self) -> Money:
        return self.planned - self.approved

    @property
    def over_budget(self) -> bool:
        return self.approved.amount_minor > self.planned.amount_minor

    def would_exceed(self, extra: Money) -> bool:
        return (self.approved + extra).amount_minor > self.planned.amount_minor
