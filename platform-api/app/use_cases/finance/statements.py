"""Student statements and the school's trial balance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.finance.billing import InvoiceStatus
from app.domain.finance.payments import PaymentStatus, RefundStatus
from app.domain.identity.roles import Realm
from app.use_cases.finance.common import (
    MoneyView,
    ensure_can_see,
    load_settings,
    require_student,
)
from app.use_cases.shared.ports import Scope, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal


@dataclass(frozen=True, slots=True)
class StatementLine:
    at: datetime
    kind: str  # invoice, invoice_void, payment, payment_reversal, refund
    reference: str
    description: str
    charge_minor: int
    credit_minor: int
    balance_minor: int  # running: positive means the student owes


@dataclass(frozen=True, slots=True)
class StatementView:
    student_id: UUID
    full_name: str
    currency: str
    invoiced: MoneyView
    paid: MoneyView
    refunded: MoneyView
    owed: MoneyView
    credit: MoneyView
    net_due: MoneyView
    lines: list[StatementLine]


@dataclass(frozen=True, slots=True)
class TrialBalanceRow:
    account: str
    currency: str
    balance_minor: int


@dataclass(frozen=True, slots=True)
class TrialBalanceView:
    rows: list[TrialBalanceRow]
    balanced: bool


class Statements:
    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def statement(self, actor: Principal, student_id: UUID) -> StatementView:
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            await ensure_can_see(uow, actor, student_id)
            student = await require_student(uow, tenant_id, student_id)
            settings = await load_settings(uow, tenant_id)
            invoices = await uow.finance.invoices_of_student(student_id)
            payments = await uow.finance.payments_of_student(student_id)
            refunds = await uow.finance.refunds_of_student(student_id)
            balance = await uow.finance.student_balance(student_id, settings.currency)

        events: list[tuple[datetime, str, str, str, int, int]] = []
        invoiced = paid = refunded = 0
        for i in invoices:
            events.append(
                (i.issued_at, "invoice", i.number, f"Invoice {i.number}", i.total.amount_minor, 0)
            )
            invoiced += i.total.amount_minor
            if i.status is InvoiceStatus.VOID and i.voided_at:
                events.append(
                    (
                        i.voided_at,
                        "invoice_void",
                        i.number,
                        f"Invoice {i.number} cancelled",
                        0,
                        i.total.amount_minor,
                    )
                )
                invoiced -= i.total.amount_minor
        for p in payments:
            label = p.method.value.replace("_", " ")
            events.append(
                (
                    p.recorded_at,
                    "payment",
                    p.receipt_number,
                    f"Payment ({label})",
                    0,
                    p.amount.amount_minor,
                )
            )
            paid += p.amount.amount_minor
            if p.status is PaymentStatus.REVERSED and p.reversed_at:
                events.append(
                    (
                        p.reversed_at,
                        "payment_reversal",
                        p.receipt_number,
                        f"Payment {p.receipt_number} reversed",
                        p.amount.amount_minor,
                        0,
                    )
                )
                paid -= p.amount.amount_minor
        for r in refunds:
            if r.status is RefundStatus.APPROVED and r.decided_at:
                events.append(
                    (r.decided_at, "refund", str(r.id)[:8], "Refund", r.amount.amount_minor, 0)
                )
                refunded += r.amount.amount_minor
        events.sort(key=lambda e: e[0])
        running = 0
        lines = []
        for at, kind, ref, desc, charge, credit in events:
            running += charge - credit
            lines.append(StatementLine(at, kind, ref, desc, charge, credit, running))
        cur = settings.currency
        return StatementView(
            student_id=student_id,
            full_name=student.full_name,
            currency=cur,
            invoiced=MoneyView(invoiced, cur),
            paid=MoneyView(paid, cur),
            refunded=MoneyView(refunded, cur),
            owed=MoneyView.of(balance.owed),
            credit=MoneyView.of(balance.credit),
            net_due=MoneyView.of(balance.net_due),
            lines=lines,
        )

    async def trial_balance(self, actor: Principal) -> TrialBalanceView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("fees:manage")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            sums = await uow.finance.trial_balance()
        rows = sorted(
            (
                TrialBalanceRow(account.value, currency, value)
                for (account, currency), value in sums.items()
            ),
            key=lambda r: (r.currency, r.account),
        )
        per_currency: dict[str, int] = {}
        for row in rows:
            per_currency[row.currency] = per_currency.get(row.currency, 0) + row.balance_minor
        return TrialBalanceView(rows=rows, balanced=all(v == 0 for v in per_currency.values()))
