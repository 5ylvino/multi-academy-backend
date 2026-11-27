"""Stock items and movements.

Quantity on hand only changes through a movement, and movements are never edited: a mistake is
corrected by another movement with a reason. Every movement stores the balance after it, so the
history explains every number on the shelf. Stock can't go below zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from app.domain.shared.entity import AggregateRoot
from app.domain.shared.errors import Conflict, ValidationFailed
from app.domain.shared.events import DomainEvent
from app.domain.shared.ids import new_id

MAX_QUANTITY = 10_000_000
UNITS = frozenset(
    {"piece", "box", "pack", "carton", "ream", "set", "pair", "bottle", "litre", "kg", "metre"}
)


def _clean(text: str | None, limit: int) -> str | None:
    value = " ".join((text or "").split())
    return value[:limit] or None


class MovementKind(StrEnum):
    RECEIVE = "receive"  # bought or donated, into the store
    ISSUE = "issue"  # handed out to a person, class or department
    RETURN = "return"  # handed back
    ADJUST = "adjust"  # a stock count found a different number
    WRITE_OFF = "write_off"  # lost, damaged or expired


#: Kinds that need a reason, because they change stock without goods visibly moving.
NEEDS_REASON = frozenset({MovementKind.ADJUST, MovementKind.WRITE_OFF})


@dataclass(frozen=True, kw_only=True)
class StockLow(DomainEvent):
    name: ClassVar[str] = "inventory.stock_low"
    item_id: UUID
    item_name: str
    on_hand: int
    reorder_level: int


@dataclass(frozen=True, slots=True)
class Movement:
    id: UUID
    tenant_id: UUID
    item_id: UUID
    kind: MovementKind
    quantity: int  # signed change
    balance_after: int
    unit_cost_minor: int | None
    reason: str | None
    issued_to: UUID | None
    reference: str | None
    by: UUID
    at: datetime


@dataclass(kw_only=True, eq=False)
class StockItem(AggregateRoot):
    tenant_id: UUID
    name: str
    sku: str | None
    category: str | None
    unit: str
    on_hand: int
    reorder_level: int
    location: str | None
    currency: str
    unit_cost_minor: int | None
    active: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        name: str,
        sku: str | None,
        category: str | None,
        unit: str,
        reorder_level: int,
        location: str | None,
        currency: str,
        now: datetime,
    ) -> StockItem:
        item = cls(
            id=new_id(),
            tenant_id=tenant_id,
            name="",
            sku=None,
            category=None,
            unit="piece",
            on_hand=0,
            reorder_level=0,
            location=None,
            currency=currency,
            unit_cost_minor=None,
            active=True,
            created_at=now,
            updated_at=now,
        )
        item.describe(
            name=name,
            sku=sku,
            category=category,
            unit=unit,
            reorder_level=reorder_level,
            location=location,
            now=now,
        )
        return item

    def describe(
        self,
        *,
        name: str,
        sku: str | None,
        category: str | None,
        unit: str,
        reorder_level: int,
        location: str | None,
        now: datetime,
    ) -> None:
        clean_name = _clean(name, 160)
        if clean_name is None or len(clean_name) < 2:
            raise ValidationFailed("Item name must be 2-160 characters.", details={"field": "name"})
        unit = unit.strip().lower()
        if unit not in UNITS:
            raise ValidationFailed(
                "Pick a unit from the list.", details={"field": "unit", "allowed": sorted(UNITS)}
            )
        if not 0 <= reorder_level <= MAX_QUANTITY:
            raise ValidationFailed(
                "Reorder level must be zero or more.", details={"field": "reorder_level"}
            )
        clean_sku = _clean(sku, 40)
        self.name = clean_name
        self.sku = clean_sku.upper() if clean_sku else None
        self.category = _clean(category, 60)
        self.unit = unit
        self.reorder_level = reorder_level
        self.location = _clean(location, 120)
        self.updated_at = now

    @property
    def is_low(self) -> bool:
        return self.active and self.on_hand <= self.reorder_level

    def move(
        self,
        *,
        kind: MovementKind,
        quantity: int,
        by: UUID,
        now: datetime,
        reason: str | None = None,
        issued_to: UUID | None = None,
        reference: str | None = None,
        unit_cost_minor: int | None = None,
    ) -> Movement:
        """``quantity`` is how many moved (always positive), except for ``adjust`` where it is
        the counted quantity now on the shelf."""
        if not self.active and kind is not MovementKind.ADJUST:
            raise ValidationFailed("This item is retired.", code="item_retired")
        if kind is MovementKind.ADJUST:
            if not 0 <= quantity <= MAX_QUANTITY:
                raise ValidationFailed("Enter the counted quantity.", details={"field": "quantity"})
            change = quantity - self.on_hand
            if change == 0:
                raise ValidationFailed(
                    "The count matches what is recorded; nothing to adjust.",
                    code="no_change",
                )
        else:
            if not 0 < quantity <= MAX_QUANTITY:
                raise ValidationFailed(
                    "Enter a quantity above zero.", details={"field": "quantity"}
                )
            change = -quantity if kind in {MovementKind.ISSUE, MovementKind.WRITE_OFF} else quantity
        if unit_cost_minor is not None and (
            kind is not MovementKind.RECEIVE or unit_cost_minor < 0
        ):
            raise ValidationFailed(
                "A unit cost goes with stock received.", details={"field": "unit_cost_minor"}
            )
        clean_reason = _clean(reason, 300)
        if kind in NEEDS_REASON and clean_reason is None:
            raise ValidationFailed("Say why.", details={"field": "reason"})
        if self.on_hand + change < 0:
            raise Conflict(
                f"Only {self.on_hand} {self.unit}(s) in stock.",
                code="insufficient_stock",
                details={"on_hand": self.on_hand, "requested": quantity},
            )
        if unit_cost_minor is not None:
            self.unit_cost_minor = unit_cost_minor
        was_low = self.is_low
        self.on_hand += change
        self.updated_at = now
        if self.is_low and not was_low:
            self.record(
                StockLow(
                    tenant_id=self.tenant_id,
                    item_id=self.id,
                    item_name=self.name,
                    on_hand=self.on_hand,
                    reorder_level=self.reorder_level,
                )
            )
        return Movement(
            id=new_id(),
            tenant_id=self.tenant_id,
            item_id=self.id,
            kind=kind,
            quantity=change,
            balance_after=self.on_hand,
            unit_cost_minor=unit_cost_minor,
            reason=clean_reason,
            issued_to=issued_to if kind in {MovementKind.ISSUE, MovementKind.RETURN} else None,
            reference=_clean(reference, 120),
            by=by,
            at=now,
        )
