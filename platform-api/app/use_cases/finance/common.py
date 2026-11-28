"""Helpers shared by the finance use cases: settings, student checks, settlement and postings.

Money changes for one student always run under ``lock_student`` so two cashiers, or a cashier
and a billing run, can never apply the same credit twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.domain.finance.billing import Invoice
from app.domain.finance.catalog import FinanceSettings
from app.domain.finance.ledger import (
    Account,
    EntryKind,
    JournalEntry,
    JournalLine,
    money_account,
)
from app.domain.finance.payments import Allocation, Payment, Refund, plan_settlement
from app.domain.identity.roles import Realm, SchoolRole
from app.domain.identity.user import UserAccount
from app.domain.shared.errors import NotFound, PermissionDenied, ValidationFailed
from app.domain.shared.money import Money, default_currency
from app.use_cases.academics.sections import ensure_student_in_scope
from app.use_cases.identity.guardians import is_guardian_or_self
from app.use_cases.shared.ports import UnitOfWork
from app.use_cases.shared.principal import Principal


@dataclass(frozen=True, slots=True)
class MoneyView:
    amount_minor: int
    currency: str

    @classmethod
    def of(cls, money: Money) -> MoneyView:
        return cls(amount_minor=money.amount_minor, currency=money.currency)


async def load_settings(uow: UnitOfWork, tenant_id: UUID) -> FinanceSettings:
    settings = await uow.finance.get_settings()
    if settings is not None:
        return settings
    tenant = await uow.tenants.get(tenant_id)
    country = tenant.country_code if tenant else "NG"
    return FinanceSettings.defaults(tenant_id, default_currency(country))


async def require_student(uow: UnitOfWork, tenant_id: UUID, student_id: UUID) -> UserAccount:
    user = await uow.users.get(student_id)
    if (
        user is None
        or user.realm is not Realm.SCHOOL
        or user.tenant_id != tenant_id
        or SchoolRole.STUDENT.value not in user.roles
    ):
        raise NotFound("Student not found.")
    return user


async def ensure_can_see(
    uow: UnitOfWork, actor: Principal, student_id: UUID, *, staff_permission: str = "invoices:read"
) -> None:
    """Finance staff see the students they serve (a bursar working per section, that section's).
    A student sees their own account, and a parent their linked children's."""
    if actor.permissions.allows("fees:view") and await is_guardian_or_self(uow, actor, student_id):
        return
    if actor.permissions.allows(staff_permission):
        # A bursar who works per section sees only that section's students.
        await ensure_student_in_scope(uow, actor, student_id)
        return
    raise PermissionDenied("You can only see your own family's fees.")


def due_date(settings: FinanceSettings, today: date, given: date | None) -> date:
    if given is not None:
        return given
    return date.fromordinal(today.toordinal() + settings.default_due_days)


def check_currency(settings: FinanceSettings, currency: str | None) -> str:
    code = (currency or settings.currency).upper()
    if code != settings.currency:
        raise ValidationFailed(
            f"This school bills in {settings.currency}.", details={"field": "currency"}
        )
    return code


# ---- ledger postings ---------------------------------------------------------------------


async def post_invoice_issued(
    uow: UnitOfWork, invoice: Invoice, *, by: UUID, now: datetime
) -> None:
    await uow.finance.post(
        JournalEntry.post(
            tenant_id=invoice.tenant_id,
            kind=EntryKind.INVOICE_ISSUED,
            source_type="invoice",
            source_id=invoice.id,
            memo=f"Invoice {invoice.number}",
            lines=[
                JournalLine(Account.RECEIVABLE, invoice.total, invoice.student_id),
                JournalLine(Account.DISCOUNTS, invoice.discount_total),
                JournalLine(Account.FEE_INCOME, -invoice.subtotal),
            ],
            by=by,
            now=now,
        )
    )


async def post_invoice_voided(
    uow: UnitOfWork, invoice: Invoice, *, by: UUID, now: datetime
) -> None:
    await uow.finance.post(
        JournalEntry.post(
            tenant_id=invoice.tenant_id,
            kind=EntryKind.INVOICE_VOIDED,
            source_type="invoice",
            source_id=invoice.id,
            memo=f"Void {invoice.number}",
            lines=[
                JournalLine(Account.RECEIVABLE, -invoice.total, invoice.student_id),
                JournalLine(Account.DISCOUNTS, -invoice.discount_total),
                JournalLine(Account.FEE_INCOME, invoice.subtotal),
            ],
            by=by,
            now=now,
        )
    )


async def post_payment(
    uow: UnitOfWork, payment: Payment, *, reverse: bool, by: UUID, now: datetime
) -> None:
    sign = -1 if reverse else 1
    amount = Money(sign * payment.amount.amount_minor, payment.amount.currency)
    await uow.finance.post(
        JournalEntry.post(
            tenant_id=payment.tenant_id,
            kind=EntryKind.PAYMENT_REVERSED if reverse else EntryKind.PAYMENT_RECORDED,
            source_type="payment",
            source_id=payment.id,
            memo=f"{'Reverse ' if reverse else ''}Receipt {payment.receipt_number}",
            lines=[
                JournalLine(money_account(payment.method), amount),
                JournalLine(Account.STUDENT_CREDIT, -amount, payment.student_id),
            ],
            by=by,
            now=now,
        )
    )


async def post_refund(uow: UnitOfWork, refund: Refund, *, by: UUID, now: datetime) -> None:
    await uow.finance.post(
        JournalEntry.post(
            tenant_id=refund.tenant_id,
            kind=EntryKind.REFUND_PAID,
            source_type="refund",
            source_id=refund.id,
            memo="Refund",
            lines=[
                JournalLine(Account.STUDENT_CREDIT, refund.amount, refund.student_id),
                JournalLine(money_account(refund.method), -refund.amount),
            ],
            by=by,
            now=now,
        )
    )


async def _post_credit_move(
    uow: UnitOfWork,
    allocation: Allocation,
    *,
    tenant_id: UUID,
    student_id: UUID,
    applied: bool,
    by: UUID | None,
    now: datetime,
) -> None:
    amount = allocation.amount if applied else -allocation.amount
    await uow.finance.post(
        JournalEntry.post(
            tenant_id=tenant_id,
            kind=EntryKind.CREDIT_APPLIED if applied else EntryKind.CREDIT_RELEASED,
            source_type="allocation",
            source_id=allocation.invoice_id,
            memo="Credit applied" if applied else "Credit released",
            lines=[
                JournalLine(Account.STUDENT_CREDIT, amount, student_id),
                JournalLine(Account.RECEIVABLE, -amount, student_id),
            ],
            by=by,
            now=now,
        )
    )


# ---- settlement --------------------------------------------------------------------------


async def settle(
    uow: UnitOfWork, *, tenant_id: UUID, student_id: UUID, by: UUID | None, now: datetime
) -> int:
    """Apply the student's unused credit to open invoices. Returns how many pairings were made.

    The caller must already hold ``lock_student`` for this student.
    """
    payments = await uow.finance.payments_with_credit(student_id)
    if not payments:
        return 0
    invoices = await uow.finance.open_invoices(student_id)
    plan = plan_settlement(payments, invoices)
    if not plan:
        return 0
    allocations: list[Allocation] = []
    for payment, invoice, amount in plan:
        payment.allocate(amount)
        invoice.apply(amount)
        allocations.append(Allocation(payment_id=payment.id, invoice_id=invoice.id, amount=amount))
    await uow.finance.add_allocations(allocations, now)
    for payment in {p.id: p for p, _, _ in plan}.values():
        await uow.finance.save_payment(payment)
    for invoice in {i.id: i for _, i, _ in plan}.values():
        await uow.finance.save_invoice(invoice)
    for allocation in allocations:
        await _post_credit_move(
            uow,
            allocation,
            tenant_id=tenant_id,
            student_id=student_id,
            applied=True,
            by=by,
            now=now,
        )
    return len(plan)


async def release(
    uow: UnitOfWork,
    *,
    tenant_id: UUID,
    student_id: UUID,
    payment_id: UUID | None = None,
    invoice_id: UUID | None = None,
    by: UUID,
    now: datetime,
) -> list[Allocation]:
    """Undo live allocations of one payment or one invoice, turning them back into credit."""
    allocations = await uow.finance.live_allocations(payment_id=payment_id, invoice_id=invoice_id)
    if not allocations:
        return []
    payments: dict[UUID, Payment] = {}
    invoices: dict[UUID, Invoice] = {}
    for allocation in allocations:
        payment = payments.get(allocation.payment_id) or await uow.finance.get_payment(
            allocation.payment_id, for_update=True
        )
        invoice = invoices.get(allocation.invoice_id) or await uow.finance.get_invoice(
            allocation.invoice_id, for_update=True
        )
        if payment is None or invoice is None:  # pragma: no cover - guarded by foreign keys
            raise NotFound("Allocation refers to missing records.")
        payments[payment.id] = payment
        invoices[invoice.id] = invoice
        payment.deallocate(allocation.amount)
        invoice.release(allocation.amount)
    await uow.finance.release_allocations(payment_id=payment_id, invoice_id=invoice_id, now=now)
    for payment in payments.values():
        await uow.finance.save_payment(payment)
    for invoice in invoices.values():
        await uow.finance.save_invoice(invoice)
    for allocation in allocations:
        await _post_credit_move(
            uow,
            allocation,
            tenant_id=tenant_id,
            student_id=student_id,
            applied=False,
            by=by,
            now=now,
        )
    return allocations
