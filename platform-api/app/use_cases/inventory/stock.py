"""Inventory: the school's stock items and every movement in or out of the store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.identity.roles import Realm
from app.domain.inventory.stock import Movement, MovementKind, StockItem
from app.domain.shared.errors import Conflict, NotFound, ValidationFailed
from app.domain.shared.value_objects import Page
from app.use_cases.finance.common import load_settings
from app.use_cases.shared import audit
from app.use_cases.shared.pagination import clamp_limit
from app.use_cases.shared.ports import Clock, Scope, UnitOfWork, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal


@dataclass(frozen=True, slots=True)
class ItemView:
    id: UUID
    name: str
    sku: str | None
    category: str | None
    unit: str
    on_hand: int
    reorder_level: int
    low: bool
    location: str | None
    currency: str
    unit_cost_minor: int | None
    value_minor: int | None
    active: bool
    updated_at: datetime

    @classmethod
    def of(cls, i: StockItem) -> ItemView:
        return cls(
            id=i.id,
            name=i.name,
            sku=i.sku,
            category=i.category,
            unit=i.unit,
            on_hand=i.on_hand,
            reorder_level=i.reorder_level,
            low=i.is_low,
            location=i.location,
            currency=i.currency,
            unit_cost_minor=i.unit_cost_minor,
            value_minor=i.on_hand * i.unit_cost_minor if i.unit_cost_minor is not None else None,
            active=i.active,
            updated_at=i.updated_at,
        )


@dataclass(frozen=True, slots=True)
class MovementView:
    id: UUID
    item_id: UUID
    kind: str
    quantity: int
    balance_after: int
    unit_cost_minor: int | None
    reason: str | None
    issued_to: UUID | None
    reference: str | None
    by: UUID
    at: datetime

    @classmethod
    def of(cls, m: Movement) -> MovementView:
        return cls(
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


@dataclass(frozen=True, slots=True)
class ItemDetails:
    name: str
    unit: str
    reorder_level: int = 0
    sku: str | None = None
    category: str | None = None
    location: str | None = None


@dataclass(frozen=True, slots=True)
class MoveCommand:
    kind: str
    quantity: int
    reason: str | None = None
    issued_to: UUID | None = None
    reference: str | None = None
    unit_cost_minor: int | None = None


@dataclass(frozen=True, slots=True)
class MoveResult:
    item: ItemView
    movement: MovementView


@dataclass(frozen=True, slots=True)
class InventorySummary:
    items: int
    low_stock: int
    value_by_currency: dict[str, int]


class Inventory:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def add_item(self, actor: Principal, details: ItemDetails) -> ItemView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("inventory:manage")
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            currency = (await load_settings(uow, tenant_id)).currency
            item = StockItem.create(
                tenant_id=tenant_id,
                name=details.name,
                sku=details.sku,
                category=details.category,
                unit=details.unit,
                reorder_level=details.reorder_level,
                location=details.location,
                currency=currency,
                now=now,
            )
            await self._unique_sku(uow, item)
            await uow.inventory.add_item(item)
            uow.audit(
                audit.by(
                    actor,
                    "inventory.item_added",
                    target_type="stock_item",
                    target_id=item.id,
                    name=item.name,
                    sku=item.sku,
                )
            )
            await uow.commit()
        return ItemView.of(item)

    async def update_item(
        self, actor: Principal, item_id: UUID, details: ItemDetails, *, active: bool | None
    ) -> ItemView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("inventory:manage")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            item = await self._item(uow, item_id, for_update=True)
            before = _fields(item)
            item.describe(
                name=details.name,
                sku=details.sku,
                category=details.category,
                unit=details.unit,
                reorder_level=details.reorder_level,
                location=details.location,
                now=self._clock.now(),
            )
            if active is not None:
                item.active = active
            await self._unique_sku(uow, item)
            await uow.inventory.save_item(item)
            uow.audit(
                audit.by(
                    actor,
                    "inventory.item_updated",
                    target_type="stock_item",
                    target_id=item.id,
                    changes=audit.changes(before, _fields(item)),
                )
            )
            await uow.commit()
        return ItemView.of(item)

    async def move(self, actor: Principal, item_id: UUID, cmd: MoveCommand) -> MoveResult:
        """Receive, issue, return, write off, or correct after a count."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("inventory:manage")
        try:
            kind = MovementKind(cmd.kind)
        except ValueError as exc:
            raise ValidationFailed("Unknown movement.", details={"field": "kind"}) from exc
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            if cmd.issued_to is not None and await uow.users.get(cmd.issued_to) is None:
                raise ValidationFailed(
                    "That person isn't in this school.", details={"field": "issued_to"}
                )
            item = await self._item(uow, item_id, for_update=True)
            before = item.on_hand
            movement = item.move(
                kind=kind,
                quantity=cmd.quantity,
                by=actor.user_id,
                now=self._clock.now(),
                reason=cmd.reason,
                issued_to=cmd.issued_to,
                reference=cmd.reference,
                unit_cost_minor=cmd.unit_cost_minor,
            )
            await uow.inventory.save_item(item)
            await uow.inventory.add_movement(movement)
            uow.track(item)
            uow.audit(
                audit.by(
                    actor,
                    f"inventory.stock_{kind.value}",
                    target_type="stock_item",
                    target_id=item.id,
                    item=item.name,
                    change=movement.quantity,
                    on_hand={"from": before, "to": item.on_hand},
                    reason=movement.reason,
                    issued_to=str(movement.issued_to) if movement.issued_to else None,
                )
            )
            await uow.commit()
        return MoveResult(item=ItemView.of(item), movement=MovementView.of(movement))

    async def get(self, actor: Principal, item_id: UUID) -> ItemView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("inventory:read")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            return ItemView.of(await self._item(uow, item_id))

    async def list(
        self,
        actor: Principal,
        *,
        search: str | None,
        category: str | None,
        low_only: bool,
        include_inactive: bool,
        cursor: str | None,
        limit: int | None,
    ) -> Page[ItemView]:
        actor.require_realm(Realm.SCHOOL)
        actor.require("inventory:read")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            page = await uow.inventory.list_items(
                search=(search or "").strip() or None,
                category=(category or "").strip() or None,
                low_only=low_only,
                include_inactive=include_inactive,
                cursor=cursor,
                limit=clamp_limit(limit),
            )
        return Page(items=[ItemView.of(i) for i in page.items], next_cursor=page.next_cursor)

    async def movements(
        self,
        actor: Principal,
        *,
        item_id: UUID | None,
        issued_to: UUID | None,
        cursor: str | None,
        limit: int | None,
    ) -> Page[MovementView]:
        actor.require_realm(Realm.SCHOOL)
        actor.require("inventory:read")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            page = await uow.inventory.movements(
                item_id=item_id, issued_to=issued_to, cursor=cursor, limit=clamp_limit(limit)
            )
        return Page(items=[MovementView.of(m) for m in page.items], next_cursor=page.next_cursor)

    async def summary(self, actor: Principal) -> InventorySummary:
        actor.require_realm(Realm.SCHOOL)
        actor.require("inventory:read")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            items, low, value = await uow.inventory.summary()
        return InventorySummary(items=items, low_stock=low, value_by_currency=value)

    @staticmethod
    async def _item(uow: UnitOfWork, item_id: UUID, *, for_update: bool = False) -> StockItem:
        item = await uow.inventory.get_item(item_id, for_update=for_update)
        if item is None:
            raise NotFound("Item not found.")
        return item

    @staticmethod
    async def _unique_sku(uow: UnitOfWork, item: StockItem) -> None:
        if item.sku and await uow.inventory.sku_exists(item.sku, other_than=item.id):
            raise Conflict("Another item has that SKU.", code="sku_exists")


def _fields(item: StockItem) -> dict[str, object]:
    return {
        "name": item.name,
        "sku": item.sku,
        "category": item.category,
        "unit": item.unit,
        "reorder_level": item.reorder_level,
        "location": item.location,
        "active": item.active,
    }
