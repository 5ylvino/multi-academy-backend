"""Inventory persistence, scoped to the current school."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.inventory.stock import Movement, StockItem
from app.domain.shared.value_objects import Page


class InventoryRepository(Protocol):
    async def add_item(self, item: StockItem) -> None: ...

    async def get_item(self, item_id: UUID, *, for_update: bool = False) -> StockItem | None: ...

    async def save_item(self, item: StockItem) -> None: ...

    async def sku_exists(self, sku: str, *, other_than: UUID | None = None) -> bool: ...

    async def list_items(
        self,
        *,
        search: str | None,
        category: str | None,
        low_only: bool,
        include_inactive: bool,
        cursor: str | None,
        limit: int,
    ) -> Page[StockItem]: ...

    async def add_movement(self, movement: Movement) -> None: ...

    async def movements(
        self,
        *,
        item_id: UUID | None,
        issued_to: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> Page[Movement]: ...

    async def summary(self) -> tuple[int, int, dict[str, int]]:
        """Active items, how many are at or below their reorder level, and stock value per
        currency (on hand x last unit cost, for items with a cost)."""
        ...
