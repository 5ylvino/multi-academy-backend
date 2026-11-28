"""Stock items and movements."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.inventory.stock import Movement, MovementKind, StockItem
from app.domain.shared.value_objects import Page
from app.infrastructure.persistence.models.inventory import StockItemRow, StockMovementRow
from app.infrastructure.persistence.repositories.finance import _decode, _encode
from app.infrastructure.persistence.repositories.scope_state import ScopeState


def _item(r: StockItemRow) -> StockItem:
    return StockItem(
        id=r.id,
        tenant_id=r.tenant_id,
        name=r.name,
        sku=r.sku,
        category=r.category,
        unit=r.unit,
        on_hand=r.on_hand,
        reorder_level=r.reorder_level,
        location=r.location,
        currency=r.currency,
        unit_cost_minor=r.unit_cost_minor,
        active=r.active,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def _movement(r: StockMovementRow) -> Movement:
    return Movement(
        id=r.id,
        tenant_id=r.tenant_id,
        item_id=r.item_id,
        kind=MovementKind(r.kind),
        quantity=r.quantity,
        balance_after=r.balance_after,
        unit_cost_minor=r.unit_cost_minor,
        reason=r.reason,
        issued_to=r.issued_to,
        reference=r.reference,
        by=r.by,
        at=r.at,
    )


def _values(i: StockItem) -> dict[str, Any]:
    return {
        "name": i.name,
        "sku": i.sku,
        "category": i.category,
        "unit": i.unit,
        "on_hand": i.on_hand,
        "reorder_level": i.reorder_level,
        "location": i.location,
        "currency": i.currency,
        "unit_cost_minor": i.unit_cost_minor,
        "active": i.active,
        "updated_at": i.updated_at,
    }


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class SqlInventoryRepository:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    @property
    def _tid(self) -> UUID:
        if self._scope.tenant_id is None:
            raise RuntimeError("Inventory needs a tenant scope.")
        return self._scope.tenant_id

    async def add_item(self, item: StockItem) -> None:
        await self._s.execute(
            pg_insert(StockItemRow).values(
                tenant_id=self._tid, id=item.id, created_at=item.created_at, **_values(item)
            )
        )

    async def get_item(self, item_id: UUID, *, for_update: bool = False) -> StockItem | None:
        stmt = select(StockItemRow).where(
            StockItemRow.tenant_id == self._tid, StockItemRow.id == item_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _item(row) if row else None

    async def save_item(self, item: StockItem) -> None:
        await self._s.execute(
            update(StockItemRow)
            .where(StockItemRow.tenant_id == self._tid, StockItemRow.id == item.id)
            .values(**_values(item))
        )

    async def sku_exists(self, sku: str, *, other_than: UUID | None = None) -> bool:
        stmt = select(StockItemRow.id).where(
            StockItemRow.tenant_id == self._tid, StockItemRow.sku == sku
        )
        if other_than is not None:
            stmt = stmt.where(StockItemRow.id != other_than)
        return (await self._s.scalar(stmt.limit(1))) is not None

    async def list_items(
        self,
        *,
        search: str | None,
        category: str | None,
        low_only: bool,
        include_inactive: bool,
        cursor: str | None,
        limit: int,
    ) -> Page[StockItem]:
        stmt = select(StockItemRow).where(StockItemRow.tenant_id == self._tid)
        if not include_inactive:
            stmt = stmt.where(StockItemRow.active.is_(True))
        if search:
            pattern = _like(search)
            stmt = stmt.where(
                StockItemRow.name.ilike(pattern, escape="\\")
                | StockItemRow.sku.ilike(pattern, escape="\\")
            )
        if category:
            stmt = stmt.where(func.lower(StockItemRow.category) == category.lower())
        if low_only:
            stmt = stmt.where(StockItemRow.on_hand <= StockItemRow.reorder_level)
        if cursor:
            stmt = stmt.where(StockItemRow.id < _decode(cursor))
        rows = list(await self._s.scalars(stmt.order_by(StockItemRow.id.desc()).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_item(r) for r in rows],
            next_cursor=_encode(rows[-1].id) if more and rows else None,
        )

    async def add_movement(self, movement: Movement) -> None:
        m = movement
        await self._s.execute(
            pg_insert(StockMovementRow).values(
                tenant_id=self._tid,
                id=m.id,
                item_id=m.item_id,
                kind=m.kind.value,
                quantity=m.quantity,
                balance_after=m.balance_after,
                unit_cost_minor=m.unit_cost_minor,
                reason=m.reason,
                issued_to=m.issued_to,
                reference=m.reference,
                by=m.by,
                at=m.at,
            )
        )

    async def movements(
        self,
        *,
        item_id: UUID | None,
        issued_to: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> Page[Movement]:
        stmt = select(StockMovementRow).where(StockMovementRow.tenant_id == self._tid)
        if item_id is not None:
            stmt = stmt.where(StockMovementRow.item_id == item_id)
        if issued_to is not None:
            stmt = stmt.where(StockMovementRow.issued_to == issued_to)
        if cursor:
            stmt = stmt.where(StockMovementRow.id < _decode(cursor))
        rows = list(
            await self._s.scalars(stmt.order_by(StockMovementRow.id.desc()).limit(limit + 1))
        )
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_movement(r) for r in rows],
            next_cursor=_encode(rows[-1].id) if more and rows else None,
        )

    async def summary(self) -> tuple[int, int, dict[str, int]]:
        counts = (
            await self._s.execute(
                select(
                    func.count(),
                    func.count().filter(StockItemRow.on_hand <= StockItemRow.reorder_level),
                ).where(StockItemRow.tenant_id == self._tid, StockItemRow.active.is_(True))
            )
        ).one()
        rows = await self._s.execute(
            select(
                StockItemRow.currency,
                func.coalesce(func.sum(StockItemRow.on_hand * StockItemRow.unit_cost_minor), 0),
            )
            .where(
                StockItemRow.tenant_id == self._tid,
                StockItemRow.active.is_(True),
                StockItemRow.unit_cost_minor.is_not(None),
            )
            .group_by(StockItemRow.currency)
        )
        value = {currency: int(amount) for currency, amount in rows.all()}
        return int(counts[0]), int(counts[1]), value
