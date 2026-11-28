"""Platform subscriptions and invoices."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, exists, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.shared.money import Money
from app.domain.shared.value_objects import Page
from app.domain.subscriptions.billing import (
    GRACE_PERIOD,
    REMIND_EVERY,
    REMIND_WINDOW,
    InvoiceStatus,
    PlatformInvoice,
    SettlementMethod,
    Subscription,
    SubscriptionStatus,
)
from app.infrastructure.persistence.models.identity import SchoolUserRow
from app.infrastructure.persistence.models.subscriptions import (
    PlatformCheckoutRow,
    PlatformInvoiceRow,
    SubscriptionRow,
)
from app.infrastructure.persistence.models.tenancy import TenantRow
from app.infrastructure.persistence.repositories.finance import _decode, _encode
from app.infrastructure.persistence.repositories.scope_state import ScopeState

#: Schools billing applies to. Suspended and offboarding schools get no new invoices.
_SERVING = ("trial", "active")


def _subscription(r: SubscriptionRow) -> Subscription:
    return Subscription(
        id=r.tenant_id,
        plan_code=r.plan_code,
        status=SubscriptionStatus(r.status),
        current_period_start=r.current_period_start,
        current_period_end=r.current_period_end,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def _invoice(r: PlatformInvoiceRow) -> PlatformInvoice:
    return PlatformInvoice(
        id=r.id,
        tenant_id=r.tenant_id,
        number=r.number,
        plan_code=r.plan_code,
        period_start=r.period_start,
        period_end=r.period_end,
        student_count=r.student_count,
        base=Money(r.base_minor, r.currency),
        per_student=Money(r.per_student_minor, r.currency),
        total=Money(r.total_minor, r.currency),
        issued_at=r.issued_at,
        due_at=r.due_at,
        status=InvoiceStatus(r.status),
        paid_at=r.paid_at,
        settlement_method=SettlementMethod(r.settlement_method) if r.settlement_method else None,
        settlement_reference=r.settlement_reference,
        settled_by=r.settled_by,
        note=r.note,
        checkout_reference=r.checkout_reference,
        authorization_url=r.authorization_url,
        checkout_started_at=r.checkout_started_at,
        reminded_at=r.reminded_at,
    )


def _mutable(i: PlatformInvoice) -> dict[str, object]:
    return {
        "status": i.status.value,
        "paid_at": i.paid_at,
        "settlement_method": i.settlement_method.value if i.settlement_method else None,
        "settlement_reference": i.settlement_reference,
        "settled_by": i.settled_by,
        "note": i.note,
        "checkout_reference": i.checkout_reference,
        "authorization_url": i.authorization_url,
        "checkout_started_at": i.checkout_started_at,
        "reminded_at": i.reminded_at,
    }


class SqlSubscriptionRepository:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    def _platform_only(self) -> None:
        if not self._scope.platform:
            raise RuntimeError("Billing runs in platform scope.")

    # ---- subscriptions -------------------------------------------------------------------

    async def get(self, tenant_id: UUID, *, for_update: bool = False) -> Subscription | None:
        stmt = select(SubscriptionRow).where(SubscriptionRow.tenant_id == tenant_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _subscription(row) if row else None

    async def save(self, subscription: Subscription) -> None:
        sub = subscription
        values = {
            "plan_code": sub.plan_code,
            "status": sub.status.value,
            "current_period_start": sub.current_period_start,
            "current_period_end": sub.current_period_end,
            "updated_at": sub.updated_at,
        }
        await self._s.execute(
            pg_insert(SubscriptionRow)
            .values(tenant_id=sub.tenant_id, created_at=sub.created_at, **values)
            .on_conflict_do_update(index_elements=["tenant_id"], set_=values)
        )

    async def schools_without_subscription(self, *, limit: int) -> list[UUID]:
        self._platform_only()
        rows = await self._s.scalars(
            select(TenantRow.id)
            .where(
                TenantRow.status.in_(_SERVING),
                ~exists().where(SubscriptionRow.tenant_id == TenantRow.id),
            )
            .order_by(TenantRow.id)
            .limit(limit)
        )
        return list(rows)

    async def due_for_invoice(self, *, before: datetime, limit: int) -> list[Subscription]:
        self._platform_only()
        has_next = exists().where(
            PlatformInvoiceRow.tenant_id == SubscriptionRow.tenant_id,
            PlatformInvoiceRow.status != "void",
            PlatformInvoiceRow.period_end > SubscriptionRow.current_period_end,
        )
        rows = await self._s.scalars(
            select(SubscriptionRow)
            .join(TenantRow, TenantRow.id == SubscriptionRow.tenant_id)
            .where(
                SubscriptionRow.status != "cancelled",
                SubscriptionRow.current_period_end < before,
                TenantRow.status.in_(_SERVING),
                ~has_next,
            )
            .order_by(SubscriptionRow.current_period_end)
            .limit(limit)
        )
        return [_subscription(r) for r in rows]

    async def next_period_invoice(
        self, tenant_id: UUID, *, after: datetime
    ) -> PlatformInvoice | None:
        row = await self._s.scalar(
            select(PlatformInvoiceRow)
            .where(
                PlatformInvoiceRow.tenant_id == tenant_id,
                PlatformInvoiceRow.status != "void",
                PlatformInvoiceRow.period_end > after,
            )
            .order_by(PlatformInvoiceRow.period_start)
            .limit(1)
        )
        return _invoice(row) if row else None

    async def count_active_students(self, tenant_id: UUID) -> int:
        count = await self._s.scalar(
            select(func.count())
            .select_from(SchoolUserRow)
            .where(
                SchoolUserRow.tenant_id == tenant_id,
                SchoolUserRow.status == "active",
                SchoolUserRow.roles.contains(["student"]),
            )
        )
        return int(count or 0)

    # ---- invoices ------------------------------------------------------------------------

    async def next_invoice_number(self) -> int:
        value = await self._s.scalar(text("SELECT nextval('platform_invoice_number_seq')"))
        return int(value)

    async def add_invoice(self, invoice: PlatformInvoice) -> None:
        i = invoice
        await self._s.execute(
            pg_insert(PlatformInvoiceRow).values(
                tenant_id=i.tenant_id,
                id=i.id,
                number=i.number,
                plan_code=i.plan_code,
                period_start=i.period_start,
                period_end=i.period_end,
                student_count=i.student_count,
                currency=i.total.currency,
                base_minor=i.base.amount_minor,
                per_student_minor=i.per_student.amount_minor,
                total_minor=i.total.amount_minor,
                issued_at=i.issued_at,
                due_at=i.due_at,
                **_mutable(i),
            )
        )

    async def get_invoice(
        self, invoice_id: UUID, *, for_update: bool = False
    ) -> PlatformInvoice | None:
        stmt = select(PlatformInvoiceRow).where(PlatformInvoiceRow.id == invoice_id)
        if self._scope.tenant_id is not None:
            stmt = stmt.where(PlatformInvoiceRow.tenant_id == self._scope.tenant_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _invoice(row) if row else None

    async def get_invoice_by_reference(
        self, reference: str, *, for_update: bool = False
    ) -> PlatformInvoice | None:
        stmt = (
            select(PlatformInvoiceRow)
            .join(
                PlatformCheckoutRow,
                and_(
                    PlatformCheckoutRow.tenant_id == PlatformInvoiceRow.tenant_id,
                    PlatformCheckoutRow.invoice_id == PlatformInvoiceRow.id,
                ),
            )
            .where(PlatformCheckoutRow.reference == reference)
        )
        if for_update:
            stmt = stmt.with_for_update(of=PlatformInvoiceRow)
        row = await self._s.scalar(stmt)
        return _invoice(row) if row else None

    async def record_checkout(
        self, *, reference: str, tenant_id: UUID, invoice_id: UUID, now: datetime
    ) -> None:
        await self._s.execute(
            pg_insert(PlatformCheckoutRow).values(
                reference=reference, tenant_id=tenant_id, invoice_id=invoice_id, created_at=now
            )
        )

    async def save_invoice(self, invoice: PlatformInvoice) -> None:
        await self._s.execute(
            update(PlatformInvoiceRow)
            .where(
                PlatformInvoiceRow.tenant_id == invoice.tenant_id,
                PlatformInvoiceRow.id == invoice.id,
            )
            .values(**_mutable(invoice))
        )

    async def list_invoices(
        self,
        *,
        tenant_id: UUID | None,
        status: InvoiceStatus | None,
        cursor: str | None,
        limit: int,
    ) -> Page[PlatformInvoice]:
        stmt = select(PlatformInvoiceRow)
        if tenant_id is not None:
            stmt = stmt.where(PlatformInvoiceRow.tenant_id == tenant_id)
        elif not self._scope.platform:
            raise RuntimeError("Listing every school's invoices needs platform scope.")
        if status is not None:
            stmt = stmt.where(PlatformInvoiceRow.status == status.value)
        if cursor:
            stmt = stmt.where(PlatformInvoiceRow.id < _decode(cursor))
        rows = list(
            await self._s.scalars(stmt.order_by(PlatformInvoiceRow.id.desc()).limit(limit + 1))
        )
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_invoice(r) for r in rows],
            next_cursor=_encode(rows[-1].id) if more and rows else None,
        )

    async def reminders_due(self, *, now: datetime, limit: int) -> list[PlatformInvoice]:
        self._platform_only()
        rows = await self._s.scalars(
            select(PlatformInvoiceRow)
            .where(
                PlatformInvoiceRow.status == "open",
                PlatformInvoiceRow.due_at < now,
                PlatformInvoiceRow.due_at >= now - REMIND_WINDOW,
                (PlatformInvoiceRow.reminded_at.is_(None))
                | (PlatformInvoiceRow.reminded_at <= now - REMIND_EVERY),
            )
            .order_by(PlatformInvoiceRow.due_at)
            .limit(limit)
        )
        return [_invoice(r) for r in rows]

    async def invoices_to_enforce(self, *, now: datetime, limit: int) -> list[PlatformInvoice]:
        self._platform_only()
        rows = await self._s.scalars(
            select(PlatformInvoiceRow)
            .join(TenantRow, TenantRow.id == PlatformInvoiceRow.tenant_id)
            .where(
                PlatformInvoiceRow.status == "open",
                PlatformInvoiceRow.due_at <= now - GRACE_PERIOD,
                TenantRow.status.in_(_SERVING),
            )
            .order_by(PlatformInvoiceRow.due_at)
            .limit(limit)
        )
        return [_invoice(r) for r in rows]

    async def invoices_awaiting_payment(
        self, *, started_before: datetime, limit: int
    ) -> list[PlatformInvoice]:
        self._platform_only()
        rows = await self._s.scalars(
            select(PlatformInvoiceRow)
            .where(
                PlatformInvoiceRow.status == "open",
                PlatformInvoiceRow.checkout_reference.is_not(None),
                PlatformInvoiceRow.checkout_started_at < started_before,
            )
            .order_by(PlatformInvoiceRow.checkout_started_at)
            .limit(limit)
        )
        return [_invoice(r) for r in rows]
