"""Inventory, end to end over HTTP."""

from __future__ import annotations

from typing import Any

import asyncpg
import httpx
import pytest

from app.infrastructure.container import Container
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer
from tests.integration.test_finance_flows import _school

pytestmark = pytest.mark.integration

INV = "/api/v1/inventory"


async def _move(
    client: httpx.AsyncClient, who: dict[str, Any], item: str, **body: Any
) -> httpx.Response:
    return await client.post(f"{INV}/items/{item}/movements", headers=bearer(who), json=body)


async def test_stock_moves_in_and_out_with_full_history(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, app_dsn: str
) -> None:
    sc = await _school(client, container, mailbox, "stores")
    keeper, teacher = sc["bursar"], sc["teacher"]
    made = await client.post(
        f"{INV}/items",
        headers=bearer(keeper),
        json={
            "name": "A4 paper",
            "unit": "ream",
            "reorder_level": 10,
            "sku": "pap-a4",
            "category": "Stationery",
        },
    )
    assert made.status_code == 201, made.text
    item = made.json()
    assert item["sku"] == "PAP-A4"
    assert item["on_hand"] == 0
    assert item["low"] is True
    dup = await client.post(
        f"{INV}/items", headers=bearer(keeper), json={"name": "Other paper", "sku": "PAP-A4"}
    )
    assert dup.status_code == 409
    bad_unit = await client.post(
        f"{INV}/items", headers=bearer(keeper), json={"name": "Chalk", "unit": "crate"}
    )
    assert bad_unit.status_code == 422

    got = await _move(
        client, keeper, item["id"], kind="receive", quantity=40, unit_cost_minor=450_000
    )
    assert got.status_code == 201, got.text
    assert got.json()["item"]["on_hand"] == 40
    assert got.json()["item"]["low"] is False
    assert got.json()["item"]["value_minor"] == 40 * 450_000

    issued = await _move(
        client, keeper, item["id"], kind="issue", quantity=25, issued_to=teacher["user_id"]
    )
    assert issued.json()["movement"]["quantity"] == -25
    assert issued.json()["movement"]["balance_after"] == 15

    too_many = await _move(client, keeper, item["id"], kind="issue", quantity=16)
    assert too_many.status_code == 409
    assert too_many.json()["code"] == "insufficient_stock"

    no_reason = await _move(client, keeper, item["id"], kind="write_off", quantity=2)
    assert no_reason.status_code == 422
    wet = await _move(
        client, keeper, item["id"], kind="write_off", quantity=2, reason="Water damage"
    )
    assert wet.json()["item"]["on_hand"] == 13

    # A count found 9 on the shelf; the correction is a movement with a reason.
    counted = await _move(
        client, keeper, item["id"], kind="adjust", quantity=9, reason="Term-end count"
    )
    assert counted.json()["movement"]["quantity"] == -4
    assert counted.json()["item"]["low"] is True
    same = await _move(client, keeper, item["id"], kind="adjust", quantity=9, reason="Recount")
    assert same.status_code == 422

    low = await client.get(f"{INV}/items", headers=bearer(keeper), params={"low_only": "true"})
    assert [i["id"] for i in low.json()["items"]] == [item["id"]]
    found = await client.get(f"{INV}/items", headers=bearer(keeper), params={"search": "a4"})
    assert len(found.json()["items"]) == 1
    summary = (await client.get(f"{INV}/summary", headers=bearer(keeper))).json()
    assert summary == {
        "items": 1,
        "low_stock": 1,
        "value_by_currency": {"NGN": 9 * 450_000},
    }

    history = (
        await client.get(f"{INV}/movements", headers=bearer(keeper), params={"item_id": item["id"]})
    ).json()["items"]
    assert [m["kind"] for m in history] == ["adjust", "write_off", "issue", "receive"]
    held = (
        await client.get(
            f"{INV}/movements", headers=bearer(keeper), params={"issued_to": teacher["user_id"]}
        )
    ).json()["items"]
    assert len(held) == 1

    # Teachers can't manage the store, and the history can't be edited.
    assert (await client.get(f"{INV}/items", headers=bearer(teacher))).status_code == 403
    conn = await asyncpg.connect(app_dsn)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE inventory_movements SET quantity = 1")
    finally:
        await conn.close()

    # Retired items keep their history and take no new movements except a count.
    item_body = {"name": "A4 paper", "unit": "ream", "reorder_level": 10, "sku": "PAP-A4"}
    retired = await client.put(
        f"{INV}/items/{item['id']}", headers=bearer(keeper), json={**item_body, "active": False}
    )
    assert retired.json()["active"] is False
    blocked = await _move(client, keeper, item["id"], kind="receive", quantity=1)
    assert blocked.status_code == 422

    trail = (
        await client.get(
            "/api/v1/audit-events", headers=bearer(sc["director"]), params={"action": "inventory."}
        )
    ).json()["items"]
    assert {"inventory.item_added", "inventory.stock_issue", "inventory.stock_adjust"} <= {
        e["action"] for e in trail
    }
