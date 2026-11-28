"""Budgets and expenses: plan spending per category per term, record what was spent, and have a
second person approve it. Approved expenses post to the ledger; voids post the reversal."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from app.domain.finance.ledger import Account, EntryKind, JournalEntry, JournalLine, money_account
from app.domain.finance.payments import PaymentMethod
from app.domain.finance.proofs import ProofFile, detect_receipt_type
from app.domain.finance.spending import (
    DEFAULT_CATEGORIES,
    EXPENSE_SERIES,
    Budget,
    CategorySpend,
    Expense,
    ExpenseCategory,
    ExpenseStatus,
    category_name,
    expense_number,
)
from app.domain.identity.roles import Realm
from app.domain.shared.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.domain.shared.money import Money
from app.domain.shared.value_objects import Page
from app.use_cases.finance.common import load_settings
from app.use_cases.shared import audit
from app.use_cases.shared.pagination import clamp_limit
from app.use_cases.shared.ports import (
    Clock,
    ObjectStore,
    SchoolTime,
    Scope,
    UnitOfWork,
    UnitOfWorkFactory,
)
from app.use_cases.shared.principal import Principal


@dataclass(frozen=True, slots=True)
class CategoryView:
    id: UUID
    name: str
    active: bool

    @classmethod
    def of(cls, c: ExpenseCategory) -> CategoryView:
        return cls(id=c.id, name=c.name, active=c.active)


@dataclass(frozen=True, slots=True)
class BudgetLineView:
    category_id: UUID
    category_name: str
    planned_minor: int
    approved_minor: int
    pending_minor: int
    remaining_minor: int
    percent_used: int | None
    over_budget: bool


@dataclass(frozen=True, slots=True)
class BudgetReportView:
    term_id: UUID
    term_name: str
    currency: str
    lines: list[BudgetLineView]
    planned_minor: int
    approved_minor: int
    pending_minor: int
    remaining_minor: int
    #: Spending in categories that have no budget for the term.
    unbudgeted_minor: int


@dataclass(frozen=True, slots=True)
class ExpenseView:
    id: UUID
    number: str
    category_id: UUID
    term_id: UUID
    amount_minor: int
    currency: str
    payee: str
    description: str
    spent_on: date
    method: str
    reference: str | None
    status: str
    has_receipt: bool
    recorded_by: UUID
    recorded_at: datetime
    decided_by: UUID | None
    decided_at: datetime | None
    decision_note: str | None
    over_budget_note: str | None
    voided_at: datetime | None
    void_reason: str | None

    @classmethod
    def of(cls, e: Expense) -> ExpenseView:
        return cls(
            id=e.id,
            number=e.number,
            category_id=e.category_id,
            term_id=e.term_id,
            amount_minor=e.amount.amount_minor,
            currency=e.amount.currency,
            payee=e.payee,
            description=e.description,
            spent_on=e.spent_on,
            method=e.method.value,
            reference=e.reference,
            status=e.status.value,
            has_receipt=e.receipt is not None,
            recorded_by=e.recorded_by,
            recorded_at=e.recorded_at,
            decided_by=e.decided_by,
            decided_at=e.decided_at,
            decision_note=e.decision_note,
            over_budget_note=e.over_budget_note,
            voided_at=e.voided_at,
            void_reason=e.void_reason,
        )


@dataclass(frozen=True, slots=True)
class RecordExpenseCommand:
    category_id: UUID
    amount_minor: int
    payee: str
    description: str
    spent_on: date
    method: str
    reference: str | None = None
    term_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ReceiptFileView:
    content: bytes
    content_type: str
    filename: str


class Spending:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        school_time: SchoolTime,
        store: ObjectStore,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._school_time = school_time
        self._store = store

    # ---- categories ----------------------------------------------------------------------

    async def categories(self, actor: Principal, *, include_inactive: bool) -> list[CategoryView]:
        """The school's categories. A school with none gets a sensible starting set."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("budgets:read")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            found = await uow.spending.list_categories(include_inactive=True)
        if not found:
            async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
                if not await uow.spending.list_categories(include_inactive=True):
                    now = self._clock.now()
                    for name in DEFAULT_CATEGORIES:
                        await uow.spending.add_category(
                            ExpenseCategory.create(tenant_id=tenant_id, name=name, now=now)
                        )
                    # audit: not needed. Seeds the built-in starting list; nothing a person chose.
                    await uow.commit()
            async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
                found = await uow.spending.list_categories(include_inactive=True)
        return [CategoryView.of(c) for c in found if include_inactive or c.active]

    async def add_category(self, actor: Principal, *, name: str) -> CategoryView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("budgets:manage")
        tenant_id = actor.school_tenant_id
        category = ExpenseCategory.create(tenant_id=tenant_id, name=name, now=self._clock.now())
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            if await uow.spending.category_name_exists(category.name):
                raise Conflict("A category with that name exists.", code="category_exists")
            await uow.spending.add_category(category)
            uow.audit(
                audit.by(
                    actor,
                    "finance.expense_category_added",
                    target_type="expense_category",
                    target_id=category.id,
                    name=category.name,
                )
            )
            await uow.commit()
        return CategoryView.of(category)

    async def update_category(
        self, actor: Principal, category_id: UUID, *, name: str | None, active: bool | None
    ) -> CategoryView:
        """Rename or retire a category. Retired categories keep their history."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("budgets:manage")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            category = await uow.spending.get_category(category_id)
            if category is None:
                raise NotFound("Category not found.")
            before = {"name": category.name, "active": category.active}
            if name is not None:
                clean = category_name(name)
                if await uow.spending.category_name_exists(clean, other_than=category.id):
                    raise Conflict("A category with that name exists.", code="category_exists")
                category.name = clean
            if active is not None:
                category.active = active
            await uow.spending.save_category(category)
            uow.audit(
                audit.by(
                    actor,
                    "finance.expense_category_updated",
                    target_type="expense_category",
                    target_id=category.id,
                    changes=audit.changes(
                        before, {"name": category.name, "active": category.active}
                    ),
                )
            )
            await uow.commit()
        return CategoryView.of(category)

    # ---- budgets -------------------------------------------------------------------------

    async def set_budget(
        self, actor: Principal, *, term_id: UUID, category_id: UUID, planned_minor: int
    ) -> BudgetLineView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("budgets:manage")
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            term = await uow.academics.get_term(term_id)
            if term is None:
                raise NotFound("Term not found.")
            category = await self._category(uow, category_id, must_be_active=True)
            currency = (await load_settings(uow, tenant_id)).currency
            planned = Money(planned_minor, currency)
            budget = await uow.spending.get_budget(term_id, category_id, for_update=True)
            previous = budget.planned.amount_minor if budget else None
            if budget is None:
                budget = Budget.plan(
                    tenant_id=tenant_id,
                    term_id=term_id,
                    category_id=category_id,
                    planned=planned,
                    by=actor.user_id,
                    now=now,
                )
            else:
                budget.replan(planned, by=actor.user_id, now=now)
            await uow.spending.save_budget(budget)
            uow.audit(
                audit.by(
                    actor,
                    "finance.budget_set",
                    target_type="budget",
                    target_id=budget.id,
                    term_id=str(term_id),
                    category=category.name,
                    planned_minor={"from": previous, "to": planned_minor},
                    currency=currency,
                )
            )
            await uow.commit()
            spend = await self._spend(uow, term_id, category_id, currency, budget)
        return _line(spend, category.name)

    async def report(self, actor: Principal, *, term_id: UUID | None) -> BudgetReportView:
        """Budget against actual for a term (the current term by default)."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("budgets:read")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            term = await (
                uow.academics.get_term(term_id) if term_id else uow.academics.current_term()
            )
            if term is None:
                raise NotFound("Term not found. Set the current term first.")
            currency = (await load_settings(uow, tenant_id)).currency
            budgets = {b.category_id: b for b in await uow.spending.budgets_for_term(term.id)}
            spend = await uow.spending.spend_by_category(term.id, currency)
            names = {
                c.id: c.name for c in await uow.spending.list_categories(include_inactive=True)
            }
        lines = []
        unbudgeted = 0
        for category_id in sorted(set(budgets) | set(spend), key=lambda c: names.get(c, "")):
            approved, pending = spend.get(category_id, (0, 0))
            budget = budgets.get(category_id)
            if budget is None:
                unbudgeted += approved
            planned = budget.planned if budget else Money(0, currency)
            row = CategorySpend(
                category_id=category_id,
                planned=planned,
                approved=Money(approved, currency),
                pending=Money(pending, currency),
            )
            lines.append(_line(row, names.get(category_id, "Unknown")))
        planned_total = sum(line.planned_minor for line in lines)
        approved_total = sum(line.approved_minor for line in lines)
        return BudgetReportView(
            term_id=term.id,
            term_name=term.name,
            currency=currency,
            lines=lines,
            planned_minor=planned_total,
            approved_minor=approved_total,
            pending_minor=sum(line.pending_minor for line in lines),
            remaining_minor=planned_total - approved_total,
            unbudgeted_minor=unbudgeted,
        )

    # ---- expenses ------------------------------------------------------------------------

    async def record(self, actor: Principal, cmd: RecordExpenseCommand) -> ExpenseView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("budgets:manage")
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        local_now = await self._school_time.now_local(tenant_id)
        try:
            method = PaymentMethod(cmd.method)
        except ValueError as exc:
            raise ValidationFailed("Pick how it was paid.", details={"field": "method"}) from exc
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            term = await (
                uow.academics.get_term(cmd.term_id) if cmd.term_id else uow.academics.current_term()
            )
            if term is None:
                raise ValidationFailed(
                    "Pick the term, or set the current term first.", details={"field": "term_id"}
                )
            await self._category(uow, cmd.category_id, must_be_active=True)
            currency = (await load_settings(uow, tenant_id)).currency
            number = expense_number(await uow.finance.next_number(EXPENSE_SERIES), local_now.year)
            expense = Expense.submit(
                tenant_id=tenant_id,
                number=number,
                category_id=cmd.category_id,
                term_id=term.id,
                amount=Money(cmd.amount_minor, currency),
                payee=cmd.payee,
                description=cmd.description,
                spent_on=cmd.spent_on,
                method=method,
                reference=cmd.reference,
                by=actor.user_id,
                today=local_now.date(),
                now=now,
            )
            await uow.spending.add_expense(expense)
            uow.track(expense)
            uow.audit(
                audit.by(
                    actor,
                    "finance.expense_recorded",
                    target_type="expense",
                    target_id=expense.id,
                    number=number,
                    amount_minor=cmd.amount_minor,
                    currency=currency,
                    payee=expense.payee,
                    method=method.value,
                )
            )
            await uow.commit()
        return ExpenseView.of(expense)

    async def attach_receipt(
        self, actor: Principal, expense_id: UUID, *, data: bytes
    ) -> ExpenseView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("budgets:manage")
        tenant_id = actor.school_tenant_id
        kind = detect_receipt_type(data)
        stored = ProofFile(
            key=Expense.receipt_key(tenant_id, expense_id, kind.extension),
            content_type=kind,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            expense = await self._expense(uow, expense_id)
            expense.attach_receipt(stored)  # fails early for a closed expense
        # Write the file first: a row pointing at a missing file is worse than an orphan file.
        await self._store.put(stored.key, data, content_type=kind.value)
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            expense = await self._expense(uow, expense_id, for_update=True)
            replaced = expense.receipt is not None
            expense.attach_receipt(stored)
            await uow.spending.save_expense(expense)
            uow.audit(
                audit.by(
                    actor,
                    "finance.expense_receipt_attached",
                    target_type="expense",
                    target_id=expense.id,
                    number=expense.number,
                    replaced=replaced,
                    size=stored.size,
                    sha256=stored.sha256,
                )
            )
            await uow.commit()
        return ExpenseView.of(expense)

    async def receipt(self, actor: Principal, expense_id: UUID) -> ReceiptFileView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("budgets:read")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            expense = await self._expense(uow, expense_id)
            if expense.receipt is None:
                raise NotFound("No receipt was attached.")
            uow.audit(
                audit.by(
                    actor,
                    "finance.expense_receipt_viewed",
                    target_type="expense",
                    target_id=expense.id,
                    number=expense.number,
                )
            )
            await uow.commit()
        receipt = expense.receipt
        content = await self._store.get(receipt.key)
        if hashlib.sha256(content).hexdigest() != receipt.sha256:
            raise Conflict("The stored receipt doesn't match what was uploaded.")
        return ReceiptFileView(
            content=content,
            content_type=receipt.content_type.value,
            filename=f"{expense.number}.{receipt.content_type.extension}",
        )

    async def approve(
        self,
        actor: Principal,
        expense_id: UUID,
        *,
        note: str | None,
        over_budget_note: str | None,
    ) -> ExpenseView:
        """Approve and post to the ledger. Going over the category's budget needs a note."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("expenses:approve")
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            peek = await self._expense(uow, expense_id)
            await uow.spending.lock_category_term(peek.term_id, peek.category_id)
            expense = await self._expense(uow, expense_id, for_update=True)
            budget = await uow.spending.get_budget(expense.term_id, expense.category_id)
            spend = await self._spend(
                uow, expense.term_id, expense.category_id, expense.amount.currency, budget
            )
            exceeds = spend.would_exceed(expense.amount)
            if exceeds and not (over_budget_note and over_budget_note.strip()):
                after = spend.approved + expense.amount
                raise Conflict(
                    "This takes the category over its budget. Add a note saying why to approve.",
                    code="over_budget",
                    details={
                        "field": "over_budget_note",
                        "planned_minor": spend.planned.amount_minor,
                        "approved_minor": spend.approved.amount_minor,
                        "after_minor": after.amount_minor,
                        "budgeted": budget is not None,
                    },
                )
            expense.approve(
                by=actor.user_id,
                now=now,
                note=note,
                over_budget_note=over_budget_note if exceeds else None,
            )
            await uow.spending.save_expense(expense)
            await self._post(uow, expense, reverse=False, by=actor.user_id, now=now)
            uow.audit(
                audit.by(
                    actor,
                    "finance.expense_approved",
                    target_type="expense",
                    target_id=expense.id,
                    number=expense.number,
                    amount_minor=expense.amount.amount_minor,
                    over_budget=exceeds,
                    over_budget_note=expense.over_budget_note,
                )
            )
            await uow.commit()
        return ExpenseView.of(expense)

    async def reject(self, actor: Principal, expense_id: UUID, *, reason: str) -> ExpenseView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("expenses:approve")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            expense = await self._expense(uow, expense_id, for_update=True)
            expense.reject(by=actor.user_id, now=self._clock.now(), reason=reason)
            await uow.spending.save_expense(expense)
            uow.audit(
                audit.by(
                    actor,
                    "finance.expense_rejected",
                    target_type="expense",
                    target_id=expense.id,
                    number=expense.number,
                    reason=expense.decision_note,
                )
            )
            await uow.commit()
        return ExpenseView.of(expense)

    async def void(self, actor: Principal, expense_id: UUID, *, reason: str) -> ExpenseView:
        """Cancel a pending expense (its recorder or an approver), or reverse an approved one
        (approvers only; posts the opposite ledger entry)."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("budgets:manage")
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            expense = await self._expense(uow, expense_id, for_update=True)
            approver = actor.permissions.allows("expenses:approve")
            if expense.status is ExpenseStatus.APPROVED and not approver:
                raise PermissionDenied("Only an approver can reverse an approved expense.")
            if (
                expense.status is ExpenseStatus.PENDING
                and expense.recorded_by != actor.user_id
                and not approver
            ):
                raise PermissionDenied("Only the person who recorded it or an approver can cancel.")
            reverse = expense.void(by=actor.user_id, now=now, reason=reason)
            await uow.spending.save_expense(expense)
            if reverse:
                await self._post(uow, expense, reverse=True, by=actor.user_id, now=now)
            uow.audit(
                audit.by(
                    actor,
                    "finance.expense_voided",
                    target_type="expense",
                    target_id=expense.id,
                    number=expense.number,
                    reversed_in_ledger=reverse,
                    reason=expense.void_reason,
                )
            )
            await uow.commit()
        return ExpenseView.of(expense)

    async def get(self, actor: Principal, expense_id: UUID) -> ExpenseView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("budgets:read")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            return ExpenseView.of(await self._expense(uow, expense_id))

    async def list(
        self,
        actor: Principal,
        *,
        term_id: UUID | None,
        category_id: UUID | None,
        status: str | None,
        cursor: str | None,
        limit: int | None,
    ) -> Page[ExpenseView]:
        actor.require_realm(Realm.SCHOOL)
        actor.require("budgets:read")
        try:
            status_filter = ExpenseStatus(status) if status else None
        except ValueError as exc:
            raise ValidationFailed("Unknown status.", details={"field": "status"}) from exc
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            page = await uow.spending.list_expenses(
                term_id=term_id,
                category_id=category_id,
                status=status_filter,
                cursor=cursor,
                limit=clamp_limit(limit),
            )
        return Page(items=[ExpenseView.of(e) for e in page.items], next_cursor=page.next_cursor)

    # ---- helpers -------------------------------------------------------------------------

    @staticmethod
    async def _category(
        uow: UnitOfWork, category_id: UUID, *, must_be_active: bool
    ) -> ExpenseCategory:
        category = await uow.spending.get_category(category_id)
        if category is None:
            raise NotFound("Category not found.")
        if must_be_active and not category.active:
            raise ValidationFailed(
                "That category is retired. Pick another.", details={"field": "category_id"}
            )
        return category

    @staticmethod
    async def _expense(uow: UnitOfWork, expense_id: UUID, *, for_update: bool = False) -> Expense:
        expense = await uow.spending.get_expense(expense_id, for_update=for_update)
        if expense is None:
            raise NotFound("Expense not found.")
        return expense

    @staticmethod
    async def _spend(
        uow: UnitOfWork,
        term_id: UUID,
        category_id: UUID,
        currency: str,
        budget: Budget | None,
    ) -> CategorySpend:
        approved, pending = (await uow.spending.spend_by_category(term_id, currency)).get(
            category_id, (0, 0)
        )
        return CategorySpend(
            category_id=category_id,
            planned=budget.planned if budget else Money(0, currency),
            approved=Money(approved, currency),
            pending=Money(pending, currency),
        )

    @staticmethod
    async def _post(
        uow: UnitOfWork, expense: Expense, *, reverse: bool, by: UUID, now: datetime
    ) -> None:
        amount = -expense.amount if reverse else expense.amount
        await uow.finance.post(
            JournalEntry.post(
                tenant_id=expense.tenant_id,
                kind=EntryKind.EXPENSE_REVERSED if reverse else EntryKind.EXPENSE_PAID,
                source_type="expense",
                source_id=expense.id,
                memo=f"{'Reverse ' if reverse else ''}{expense.number} {expense.payee}",
                lines=[
                    JournalLine(Account.EXPENSES, amount),
                    JournalLine(money_account(expense.method), -amount),
                ],
                by=by,
                now=now,
            )
        )


def _line(spend: CategorySpend, name: str) -> BudgetLineView:
    planned = spend.planned.amount_minor
    return BudgetLineView(
        category_id=spend.category_id,
        category_name=name,
        planned_minor=planned,
        approved_minor=spend.approved.amount_minor,
        pending_minor=spend.pending.amount_minor,
        remaining_minor=spend.remaining.amount_minor,
        percent_used=round(spend.approved.amount_minor * 100 / planned) if planned else None,
        over_budget=spend.over_budget,
    )
