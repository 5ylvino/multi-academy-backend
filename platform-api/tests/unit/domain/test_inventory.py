from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.inventory.stock import MovementKind, StockItem, StockLow
from app.domain.shared.errors import Conflict, ValidationFailed

NOW = datetime(2026, 11, 27, tzinfo=UTC)


def _item(reorder: int = 5) -> StockItem:
    return StockItem.create(
        tenant_id=uuid4(),
        name="Chalk",
        sku=" ch-1 ",
        category=None,
        unit="Box",
        reorder_level=reorder,
        location=None,
        currency="NGN",
        now=NOW,
    )


def test_create_normalises() -> None:
    item = _item()
    assert item.sku == "CH-1"
    assert item.unit == "box"
    assert item.on_hand == 0


def test_movements_change_stock_and_flag_low_once() -> None:
    item = _item(reorder=5)
    by = uuid4()
    item.move(kind=MovementKind.RECEIVE, quantity=10, by=by, now=NOW, unit_cost_minor=100)
    assert item.on_hand == 10
    item.pull_events()
    item.move(kind=MovementKind.ISSUE, quantity=5, by=by, now=NOW)
    [event] = item.pull_events()
    assert isinstance(event, StockLow)
    item.move(kind=MovementKind.ISSUE, quantity=1, by=by, now=NOW)
    assert item.pull_events() == []  # already low: no repeat alert
    back = item.move(kind=MovementKind.RETURN, quantity=2, by=by, now=NOW, issued_to=uuid4())
    assert back.quantity == 2
    assert item.on_hand == 6


def test_guards() -> None:
    item = _item()
    by = uuid4()
    with pytest.raises(Conflict):
        item.move(kind=MovementKind.ISSUE, quantity=1, by=by, now=NOW)
    with pytest.raises(ValidationFailed):
        item.move(kind=MovementKind.RECEIVE, quantity=0, by=by, now=NOW)
    with pytest.raises(ValidationFailed):
        item.move(kind=MovementKind.ISSUE, quantity=1, by=by, now=NOW, unit_cost_minor=5)
    with pytest.raises(ValidationFailed):
        item.move(kind=MovementKind.ADJUST, quantity=3, by=by, now=NOW)  # no reason
    m = item.move(kind=MovementKind.ADJUST, quantity=3, by=by, now=NOW, reason="count")
    assert m.quantity == 3
    assert m.issued_to is None
