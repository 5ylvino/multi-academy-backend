"""Double-entry ledger.

Every money movement posts one journal entry whose lines sum to zero (debits positive, credits
negative). Entries are append-only: a mistake is corrected by a new, opposite entry. Student
balances come from the ledger, so the books and what parents see can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.finance.payments import PaymentMethod
from app.domain.shared.errors import ValidationFailed
from app.domain.shared.ids import new_id
from app.domain.shared.money import Money, total


class Account(StrEnum):
    RECEIVABLE = "receivable"  # asset: what students owe (per student)
    STUDENT_CREDIT = "student_credit"  # liability: money held for a student (per student)
    FEE_INCOME = "fee_income"  # income
    DISCOUNTS = "discounts"  # contra-income: scholarships
    CASH = "cash"  # asset
    BANK = "bank"  # asset: transfers, POS and cheques
    GATEWAY_CLEARING = "gateway_clearing"  # asset: online money not yet settled to the bank
    EXPENSES = "expenses"  # expense: approved school spending


PER_STUDENT = frozenset({Account.RECEIVABLE, Account.STUDENT_CREDIT})


def money_account(method: PaymentMethod) -> Account:
    if method is PaymentMethod.CASH:
        return Account.CASH
    if method is PaymentMethod.ONLINE:
        return Account.GATEWAY_CLEARING
    return Account.BANK


class EntryKind(StrEnum):
    INVOICE_ISSUED = "invoice_issued"
    INVOICE_VOIDED = "invoice_voided"
    PAYMENT_RECORDED = "payment_recorded"
    PAYMENT_REVERSED = "payment_reversed"
    CREDIT_APPLIED = "credit_applied"
    CREDIT_RELEASED = "credit_released"
    REFUND_PAID = "refund_paid"
    EXPENSE_PAID = "expense_paid"
    EXPENSE_REVERSED = "expense_reversed"


@dataclass(frozen=True, slots=True)
class JournalLine:
    account: Account
    amount: Money  # debit positive, credit negative
    student_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class JournalEntry:
    id: UUID
    tenant_id: UUID
    kind: EntryKind
    source_type: str
    source_id: UUID
    memo: str
    lines: tuple[JournalLine, ...]
    posted_by: UUID | None
    posted_at: datetime

    @classmethod
    def post(
        cls,
        *,
        tenant_id: UUID,
        kind: EntryKind,
        source_type: str,
        source_id: UUID,
        memo: str,
        lines: list[JournalLine],
        by: UUID | None,
        now: datetime,
    ) -> JournalEntry:
        kept = [line for line in lines if not line.amount.is_zero]
        if len(kept) < 2:
            raise ValidationFailed("A journal entry needs at least two lines.")
        currency = kept[0].amount.currency
        if total([line.amount for line in kept], currency).amount_minor != 0:
            raise ValidationFailed("Journal entry does not balance.", code="unbalanced_entry")
        for line in kept:
            if (line.account in PER_STUDENT) != (line.student_id is not None):
                raise ValidationFailed("Student accounts need a student, others must not.")
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            kind=kind,
            source_type=source_type,
            source_id=source_id,
            memo=memo[:200],
            lines=tuple(kept),
            posted_by=by,
            posted_at=now,
        )


@dataclass(frozen=True, slots=True)
class StudentBalance:
    """From the ledger: ``owed`` is unpaid invoices, ``credit`` is unused money held."""

    owed: Money
    credit: Money

    @property
    def net_due(self) -> Money:
        return self.owed - self.credit
