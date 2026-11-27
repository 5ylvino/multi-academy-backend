"""Term billing runs, one-off invoices, invoice lookup and voiding."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from masms.domain.finance.billing import (
    Invoice,
    InvoiceKind,
    InvoiceLine,
    InvoiceStatus,
    Scholarship,
)
from masms.domain.finance.catalog import FeeItem
from masms.domain.identity.roles import Realm
from masms.domain.shared.errors import NotFound, PermissionDenied, ValidationFailed
from masms.domain.shared.value_objects import Page
from masms.use_cases.finance.common import (
    MoneyView,
    due_date,
    ensure_can_see,
    load_settings,
    post_invoice_issued,
    post_invoice_voided,
    release,
    require_student,
    settle,
)
from masms.use_cases.shared.ports import (
    AuditEntry,
    Clock,
    SchoolTime,
    Scope,
    UnitOfWork,
    UnitOfWorkFactory,
)
from masms.use_cases.shared.principal import Principal

#: One run bills one term for at most this many students. Larger schools run it per class.
MAX_RUN_STUDENTS = 3000
INVOICE_SERIES = "invoice"


def invoice_number(value: int, year: int) -> str:
    return f"INV-{year}-{value:06d}"


@dataclass(frozen=True, slots=True)
class LineView:
    description: str
    amount: MoneyView
    fee_item_id: UUID | None


@dataclass(frozen=True, slots=True)
class DiscountView:
    description: str
    amount: MoneyView
    scholarship_id: UUID | None


@dataclass(frozen=True, slots=True)
class InvoiceView:
    id: UUID
    number: str
    kind: str
    status: str
    student_id: UUID
    class_id: UUID | None
    session_id: UUID | None
    term_id: UUID | None
    lines: list[LineView]
    discounts: list[DiscountView]
    subtotal: MoneyView
    discount_total: MoneyView
    total: MoneyView
    amount_paid: MoneyView
    balance: MoneyView
    due_on: date
    issued_at: datetime
    voided_at: datetime | None
    void_reason: str | None

    @classmethod
    def of(cls, i: Invoice) -> InvoiceView:
        return cls(
            id=i.id,
            number=i.number,
            kind=i.kind.value,
            status=i.status.value,
            student_id=i.student_id,
            class_id=i.class_id,
            session_id=i.session_id,
            term_id=i.term_id,
            lines=[LineView(x.description, MoneyView.of(x.amount), x.fee_item_id) for x in i.lines],
            discounts=[
                DiscountView(d.description, MoneyView.of(d.amount), d.scholarship_id)
                for d in i.discounts
            ],
            subtotal=MoneyView.of(i.subtotal),
            discount_total=MoneyView.of(i.discount_total),
            total=MoneyView.of(i.total),
            amount_paid=MoneyView.of(i.amount_paid),
            balance=MoneyView.of(i.balance),
            due_on=i.due_on,
            issued_at=i.issued_at,
            voided_at=i.voided_at,
            void_reason=i.void_reason,
        )


@dataclass(frozen=True, slots=True)
class BillingRunResult:
    term_id: UUID
    invoiced: int
    already_billed: int
    nothing_to_bill: int
    total_billed: MoneyView


class Billing:
    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, clock: Clock, school_time: SchoolTime
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._school_time = school_time

    async def _today(self, tenant_id: UUID) -> date:
        return (await self._school_time.now_local(tenant_id)).date()

    async def _number(self, uow: UnitOfWork, today: date) -> str:
        return invoice_number(await uow.finance.next_number(INVOICE_SERIES), today.year)

    async def billing_run(
        self,
        actor: Principal,
        *,
        term_id: UUID | None,
        class_ids: list[UUID] | None,
        due_on: date | None,
    ) -> BillingRunResult:
        """Bill every enrolled student for the term's mandatory fees.

        Safe to repeat: students who already have a live invoice for the term are skipped, so a
        second run after new enrolments bills only the newcomers.
        """
        actor.require_realm(Realm.SCHOOL)
        actor.require("invoices:create")
        tenant_id = actor.school_tenant_id
        today = await self._today(tenant_id)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            term = (
                await uow.academics.get_term(term_id)
                if term_id
                else await uow.academics.current_term()
            )
            if term is None and term_id:
                raise NotFound("Term not found.")
            if term is None:
                raise ValidationFailed("Set the current term first.", code="no_current_term")
            settings = await load_settings(uow, tenant_id)
            due = due_date(settings, today, due_on)
            classes = {
                c.id: c
                for c in await uow.academics.list_classes(level=None, include_inactive=False)
            }
            if class_ids:
                unknown = [str(c) for c in class_ids if c not in classes]
                if unknown:
                    raise NotFound("Some classes were not found.", details={"class_ids": unknown})
                classes = {c: classes[c] for c in class_ids}
            items = [
                i
                for i in await uow.finance.list_fee_items(include_inactive=False)
                if i.mandatory and i.amount.currency == settings.currency
            ]
            scholarships: dict[UUID, list[Scholarship]] = {}
            for s in await uow.finance.scholarships_for(
                student_id=None, session_id=term.session_id, active_only=True
            ):
                scholarships.setdefault(s.student_id, []).append(s)
            billed = await uow.finance.students_billed_for_term(term.id)

            enrolments = []
            for class_id in classes:
                enrolments.extend(await uow.academics.roster(class_id, term.session_id))
            if len(enrolments) > MAX_RUN_STUDENTS:
                raise ValidationFailed(
                    f"Bill at most {MAX_RUN_STUDENTS} students at once. Pick some classes.",
                    details={"field": "class_ids"},
                )
            invoices: list[Invoice] = []
            already = nothing = 0
            for enrolment in enrolments:
                if enrolment.student_id in billed:
                    already += 1
                    continue
                school_class = classes[enrolment.class_id]
                lines = [
                    InvoiceLine(description=i.name, amount=i.amount, fee_item_id=i.id)
                    for i in items
                    if i.applies_to(
                        class_id=school_class.id,
                        school_level=school_class.school_level.value,
                        term=term.code.value,
                    )
                ]
                if not lines:
                    nothing += 1
                    continue
                invoices.append(
                    Invoice.issue(
                        tenant_id=tenant_id,
                        number=await self._number(uow, today),
                        kind=InvoiceKind.TERM,
                        student_id=enrolment.student_id,
                        class_id=school_class.id,
                        session_id=term.session_id,
                        term_id=term.id,
                        currency=settings.currency,
                        lines=lines,
                        scholarships=scholarships.get(enrolment.student_id, []),
                        due_on=due,
                        today=today,
                        by=actor.user_id,
                        now=now,
                    )
                )
            total_minor = 0
            if invoices:
                await uow.finance.add_invoices(invoices)
                for invoice in invoices:
                    uow.track(invoice)
                    total_minor += invoice.total.amount_minor
                    await post_invoice_issued(uow, invoice, by=actor.user_id, now=now)
                for student_id in sorted({i.student_id for i in invoices}, key=str):
                    await uow.finance.lock_student(student_id)
                    await settle(
                        uow, tenant_id=tenant_id, student_id=student_id, by=actor.user_id, now=now
                    )
            uow.audit(
                AuditEntry(
                    action="finance.billing_run",
                    actor_id=actor.user_id,
                    actor_realm=Realm.SCHOOL,
                    tenant_id=tenant_id,
                    target_type="term",
                    target_id=str(term.id),
                    metadata={
                        "invoiced": len(invoices),
                        "already_billed": already,
                        "total_minor": total_minor,
                        "class_ids": [str(c) for c in classes],
                    },
                )
            )
            await uow.commit()
        return BillingRunResult(
            term_id=term.id,
            invoiced=len(invoices),
            already_billed=already,
            nothing_to_bill=nothing,
            total_billed=MoneyView(total_minor, settings.currency),
        )

    async def create_invoice(
        self,
        actor: Principal,
        *,
        student_id: UUID,
        fee_item_ids: list[UUID],
        due_on: date | None,
    ) -> InvoiceView:
        """A one-off invoice from catalog items (optional extras, uniforms, trips)."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("invoices:create")
        if not fee_item_ids or len(set(fee_item_ids)) != len(fee_item_ids):
            raise ValidationFailed("Pick each fee item once.", details={"field": "fee_item_ids"})
        tenant_id = actor.school_tenant_id
        today = await self._today(tenant_id)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await require_student(uow, tenant_id, student_id)
            settings = await load_settings(uow, tenant_id)
            items: list[FeeItem] = []
            for item_id in fee_item_ids:
                item = await uow.finance.get_fee_item(item_id)
                if item is None or not item.active:
                    raise NotFound("Fee item not found.", details={"fee_item_id": str(item_id)})
                items.append(item)
            term = await uow.academics.current_term()
            class_id = (
                await uow.academics.class_of_student(student_id, term.session_id) if term else None
            )
            invoice = Invoice.issue(
                tenant_id=tenant_id,
                number=await self._number(uow, today),
                kind=InvoiceKind.ADHOC,
                student_id=student_id,
                class_id=class_id,
                session_id=term.session_id if term else None,
                term_id=term.id if term else None,
                currency=settings.currency,
                lines=[
                    InvoiceLine(description=i.name, amount=i.amount, fee_item_id=i.id)
                    for i in items
                ],
                scholarships=[],
                due_on=due_date(settings, today, due_on),
                today=today,
                by=actor.user_id,
                now=now,
            )
            await uow.finance.lock_student(student_id)
            await uow.finance.add_invoices([invoice])
            uow.track(invoice)
            await post_invoice_issued(uow, invoice, by=actor.user_id, now=now)
            await settle(uow, tenant_id=tenant_id, student_id=student_id, by=actor.user_id, now=now)
            fresh = await uow.finance.get_invoice(invoice.id)
            await uow.commit()
        return InvoiceView.of(fresh or invoice)

    async def get_invoice(self, actor: Principal, invoice_id: UUID) -> InvoiceView:
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            invoice = await uow.finance.get_invoice(invoice_id)
            if invoice is None:
                raise NotFound("Invoice not found.")
            try:
                await ensure_can_see(uow, actor, invoice.student_id)
            except PermissionDenied as exc:
                raise NotFound("Invoice not found.") from exc
        return InvoiceView.of(invoice)

    async def list_invoices(
        self,
        actor: Principal,
        *,
        student_id: UUID | None,
        term_id: UUID | None,
        status: str | None,
        cursor: str | None,
        limit: int,
    ) -> Page[InvoiceView]:
        actor.require_realm(Realm.SCHOOL)
        try:
            status_filter = InvoiceStatus(status) if status else None
        except ValueError as exc:
            raise ValidationFailed("Unknown status.", details={"field": "status"}) from exc
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            if not actor.permissions.allows("invoices:read"):
                # Students list their own invoices; parents pick one of their children.
                student_id = student_id or actor.user_id
                await ensure_can_see(uow, actor, student_id)
            page = await uow.finance.list_invoices(
                student_id=student_id,
                term_id=term_id,
                status=status_filter,
                cursor=cursor,
                limit=max(1, min(limit, 100)),
            )
        return Page(items=[InvoiceView.of(i) for i in page.items], next_cursor=page.next_cursor)

    async def void_invoice(self, actor: Principal, invoice_id: UUID, *, reason: str) -> InvoiceView:
        """Cancel an invoice. Anything paid on it goes back to the student as credit, which
        then settles their other open invoices."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("fees:manage")
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            found = await uow.finance.get_invoice(invoice_id)
            if found is None:
                raise NotFound("Invoice not found.")
            await uow.finance.lock_student(found.student_id)
            released = await release(
                uow,
                tenant_id=tenant_id,
                student_id=found.student_id,
                invoice_id=invoice_id,
                by=actor.user_id,
                now=now,
            )
            invoice = await uow.finance.get_invoice(invoice_id, for_update=True)
            if invoice is None:  # pragma: no cover - locked above
                raise NotFound("Invoice not found.")
            invoice.void(reason=reason, by=actor.user_id, now=now)
            await uow.finance.save_invoice(invoice)
            await post_invoice_voided(uow, invoice, by=actor.user_id, now=now)
            await settle(
                uow, tenant_id=tenant_id, student_id=invoice.student_id, by=actor.user_id, now=now
            )
            uow.audit(
                AuditEntry(
                    action="finance.invoice_voided",
                    actor_id=actor.user_id,
                    actor_realm=Realm.SCHOOL,
                    tenant_id=tenant_id,
                    target_type="invoice",
                    target_id=str(invoice.id),
                    metadata={
                        "number": invoice.number,
                        "total_minor": invoice.total.amount_minor,
                        "released_minor": sum(a.amount.amount_minor for a in released),
                        "reason": invoice.void_reason,
                    },
                )
            )
            await uow.commit()
        return InvoiceView.of(invoice)
