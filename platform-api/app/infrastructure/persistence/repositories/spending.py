"""Expense categories, budgets and expenses."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.finance.payments import PaymentMethod
from app.domain.finance.proofs import ProofFile, ReceiptType
from app.domain.finance.spending import Budget, Expense, ExpenseCategory, ExpenseStatus
from app.domain.shared.money import Money
from app.domain.shared.value_objects import Page
from app.infrastructure.persistence.models.finance import (
    BudgetRow,
    ExpenseCategoryRow,
    ExpenseRow,
)
from app.infrastructure.persistence.repositories.finance import _decode, _encode
from app.infrastructure.persistence.repositories.scope_state import ScopeState


def _category(r: ExpenseCategoryRow) -> ExpenseCategory:
    return ExpenseCategory(
        id=r.id, tenant_id=r.tenant_id, name=r.name, active=r.active, created_at=r.created_at
    )


def _budget(r: BudgetRow) -> Budget:
    return Budget(
        id=r.id,
        tenant_id=r.tenant_id,
        term_id=r.term_id,
        category_id=r.category_id,
        planned=Money(r.planned_minor, r.currency),
        updated_by=r.updated_by,
        updated_at=r.updated_at,
    )


def _expense(r: ExpenseRow) -> Expense:
    receipt = None
    if r.receipt_key and r.receipt_type and r.receipt_size and r.receipt_sha256:
        receipt = ProofFile(
            key=r.receipt_key,
            content_type=ReceiptType(r.receipt_type),
            size=r.receipt_size,
            sha256=r.receipt_sha256,
        )
    return Expense(
        id=r.id,
        tenant_id=r.tenant_id,
        number=r.number,
        category_id=r.category_id,
        term_id=r.term_id,
        amount=Money(r.amount_minor, r.currency),
        payee=r.payee,
        description=r.description,
        spent_on=r.spent_on,
        method=PaymentMethod(r.method),
        reference=r.reference,
        recorded_by=r.recorded_by,
        recorded_at=r.recorded_at,
        status=ExpenseStatus(r.status),
        receipt=receipt,
        decided_by=r.decided_by,
        decided_at=r.decided_at,
        decision_note=r.decision_note,
        over_budget_note=r.over_budget_note,
        voided_by=r.voided_by,
        voided_at=r.voided_at,
        void_reason=r.void_reason,
    )


def _mutable(e: Expense) -> dict[str, Any]:
    return {
        "category_id": e.category_id,
        "status": e.status.value,
        "receipt_key": e.receipt.key if e.receipt else None,
        "receipt_type": e.receipt.content_type.value if e.receipt else None,
        "receipt_size": e.receipt.size if e.receipt else None,
        "receipt_sha256": e.receipt.sha256 if e.receipt else None,
        "decided_by": e.decided_by,
        "decided_at": e.decided_at,
        "decision_note": e.decision_note,
        "over_budget_note": e.over_budget_note,
        "voided_by": e.voided_by,
        "voided_at": e.voided_at,
        "void_reason": e.void_reason,
    }


class SqlSpendingRepository:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    @property
    def _tid(self) -> UUID:
        if self._scope.tenant_id is None:
            raise RuntimeError("Spending needs a tenant scope.")
        return self._scope.tenant_id

    # ---- categories ----------------------------------------------------------------------

    async def add_category(self, category: ExpenseCategory) -> None:
        await self._s.execute(
            pg_insert(ExpenseCategoryRow).values(
                tenant_id=self._tid,
                id=category.id,
                name=category.name,
                active=category.active,
                created_at=category.created_at,
            )
        )

    async def get_category(self, category_id: UUID) -> ExpenseCategory | None:
        row = await self._s.scalar(
            select(ExpenseCategoryRow).where(
                ExpenseCategoryRow.tenant_id == self._tid, ExpenseCategoryRow.id == category_id
            )
        )
        return _category(row) if row else None

    async def save_category(self, category: ExpenseCategory) -> None:
        await self._s.execute(
            update(ExpenseCategoryRow)
            .where(ExpenseCategoryRow.tenant_id == self._tid, ExpenseCategoryRow.id == category.id)
            .values(name=category.name, active=category.active)
        )

    async def list_categories(self, *, include_inactive: bool) -> list[ExpenseCategory]:
        stmt = select(ExpenseCategoryRow).where(ExpenseCategoryRow.tenant_id == self._tid)
        if not include_inactive:
            stmt = stmt.where(ExpenseCategoryRow.active.is_(True))
        rows = await self._s.scalars(stmt.order_by(func.lower(ExpenseCategoryRow.name)))
        return [_category(r) for r in rows]

    async def category_name_exists(self, name: str, *, other_than: UUID | None = None) -> bool:
        stmt = select(ExpenseCategoryRow.id).where(
            ExpenseCategoryRow.tenant_id == self._tid,
            func.lower(ExpenseCategoryRow.name) == name.lower(),
        )
        if other_than is not None:
            stmt = stmt.where(ExpenseCategoryRow.id != other_than)
        return (await self._s.scalar(stmt.limit(1))) is not None

    # ---- budgets -------------------------------------------------------------------------

    async def get_budget(
        self, term_id: UUID, category_id: UUID, *, for_update: bool = False
    ) -> Budget | None:
        stmt = select(BudgetRow).where(
            BudgetRow.tenant_id == self._tid,
            BudgetRow.term_id == term_id,
            BudgetRow.category_id == category_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _budget(row) if row else None

    async def save_budget(self, budget: Budget) -> None:
        values = {
            "planned_minor": budget.planned.amount_minor,
            "currency": budget.planned.currency,
            "updated_by": budget.updated_by,
            "updated_at": budget.updated_at,
        }
        await self._s.execute(
            pg_insert(BudgetRow)
            .values(
                tenant_id=self._tid,
                id=budget.id,
                term_id=budget.term_id,
                category_id=budget.category_id,
                **values,
            )
            .on_conflict_do_update(constraint="uq_finance_budgets_line", set_=values)
        )

    async def budgets_for_term(self, term_id: UUID) -> list[Budget]:
        rows = await self._s.scalars(
            select(BudgetRow).where(BudgetRow.tenant_id == self._tid, BudgetRow.term_id == term_id)
        )
        return [_budget(r) for r in rows]

    # ---- expenses ------------------------------------------------------------------------

    async def add_expense(self, expense: Expense) -> None:
        e = expense
        await self._s.execute(
            pg_insert(ExpenseRow).values(
                tenant_id=self._tid,
                id=e.id,
                number=e.number,
                term_id=e.term_id,
                amount_minor=e.amount.amount_minor,
                currency=e.amount.currency,
                payee=e.payee,
                description=e.description,
                spent_on=e.spent_on,
                method=e.method.value,
                reference=e.reference,
                recorded_by=e.recorded_by,
                recorded_at=e.recorded_at,
                **_mutable(e),
            )
        )

    async def get_expense(self, expense_id: UUID, *, for_update: bool = False) -> Expense | None:
        stmt = select(ExpenseRow).where(
            ExpenseRow.tenant_id == self._tid, ExpenseRow.id == expense_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _expense(row) if row else None

    async def save_expense(self, expense: Expense) -> None:
        await self._s.execute(
            update(ExpenseRow)
            .where(ExpenseRow.tenant_id == self._tid, ExpenseRow.id == expense.id)
            .values(**_mutable(expense))
        )

    async def list_expenses(
        self,
        *,
        term_id: UUID | None,
        category_id: UUID | None,
        status: ExpenseStatus | None,
        cursor: str | None,
        limit: int,
    ) -> Page[Expense]:
        stmt = select(ExpenseRow).where(ExpenseRow.tenant_id == self._tid)
        if term_id is not None:
            stmt = stmt.where(ExpenseRow.term_id == term_id)
        if category_id is not None:
            stmt = stmt.where(ExpenseRow.category_id == category_id)
        if status is not None:
            stmt = stmt.where(ExpenseRow.status == status.value)
        if cursor:
            stmt = stmt.where(ExpenseRow.id < _decode(cursor))
        rows = list(await self._s.scalars(stmt.order_by(ExpenseRow.id.desc()).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_expense(r) for r in rows],
            next_cursor=_encode(rows[-1].id) if more and rows else None,
        )

    async def spend_by_category(self, term_id: UUID, currency: str) -> dict[UUID, tuple[int, int]]:
        approved = func.coalesce(
            func.sum(case((ExpenseRow.status == "approved", ExpenseRow.amount_minor), else_=0)), 0
        )
        pending = func.coalesce(
            func.sum(case((ExpenseRow.status == "pending", ExpenseRow.amount_minor), else_=0)), 0
        )
        rows = await self._s.execute(
            select(ExpenseRow.category_id, approved, pending)
            .where(
                ExpenseRow.tenant_id == self._tid,
                ExpenseRow.term_id == term_id,
                ExpenseRow.currency == currency,
            )
            .group_by(ExpenseRow.category_id)
        )
        return {cid: (int(a), int(p)) for cid, a, p in rows.all()}

    async def lock_category_term(self, term_id: UUID, category_id: UUID) -> None:
        await self._s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"budget:{self._tid}:{term_id}:{category_id}"},
        )
