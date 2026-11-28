"""Budgets and expenses, end to end over HTTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.infrastructure.container import Container
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer
from tests.integration.test_finance_flows import FIN, _balanced, _school
from tests.integration.test_payment_proofs_flows import PDF

pytestmark = pytest.mark.integration

TODAY = datetime.now(UTC).date()


async def _category(client: httpx.AsyncClient, who: dict[str, Any], name: str) -> str:
    cats = (await client.get(f"{FIN}/expense-categories", headers=bearer(who))).json()
    return str(next(c["id"] for c in cats if c["name"] == name))


async def _expense(
    client: httpx.AsyncClient, who: dict[str, Any], category_id: str, amount: int, **extra: Any
) -> httpx.Response:
    body = {
        "category_id": category_id,
        "amount_minor": amount,
        "payee": "Ikeja Electric",
        "description": "Electricity for October",
        "spent_on": str(TODAY - timedelta(days=1)),
        "method": "bank_transfer",
        "reference": "TRF-778812",
        **extra,
    }
    return await client.post(f"{FIN}/expenses", headers=bearer(who), json=body)


async def test_budget_expense_approval_and_ledger(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _school(client, container, mailbox, "spender")
    bursar, head = sc["bursar"], sc["director"]

    cats = (await client.get(f"{FIN}/expense-categories", headers=bearer(bursar))).json()
    assert "Utilities" in {c["name"] for c in cats}  # a starting set is ready
    utilities = await _category(client, bursar, "Utilities")

    budget = await client.put(
        f"{FIN}/budgets",
        headers=bearer(bursar),
        json={"term_id": sc["term_id"], "category_id": utilities, "planned_minor": 10_000_000},
    )
    assert budget.status_code == 200, budget.text
    assert budget.json()["remaining_minor"] == 10_000_000

    # Recording needs a reference for a transfer, and a date that isn't in the future.
    no_ref = await _expense(client, bursar, utilities, 100, reference=None)
    assert no_ref.status_code == 422
    future = await _expense(client, bursar, utilities, 100, spent_on=str(TODAY + timedelta(days=2)))
    assert future.status_code == 422

    first = await _expense(client, bursar, utilities, 6_000_000)
    assert first.status_code == 201, first.text
    e1 = first.json()
    assert e1["status"] == "pending"
    assert e1["number"].startswith("EXP-")
    assert e1["term_id"] == sc["term_id"]  # current term by default

    report = (await client.get(f"{FIN}/budgets", headers=bearer(head))).json()
    [line] = [x for x in report["lines"] if x["category_id"] == utilities]
    assert line["pending_minor"] == 6_000_000
    assert line["approved_minor"] == 0

    # The person who recorded it can't approve it.
    self_approve = await client.post(
        f"{FIN}/expenses/{e1['id']}/approve", headers=bearer(bursar), json={}
    )
    assert self_approve.status_code == 403
    approved = await client.post(
        f"{FIN}/expenses/{e1['id']}/approve", headers=bearer(head), json={}
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    books = await _balanced(client, bursar)
    assert books["expenses"] == 6_000_000
    assert books["bank"] == -6_000_000

    # Going over budget needs a reason.
    second = (await _expense(client, bursar, utilities, 5_000_000)).json()
    over = await client.post(
        f"{FIN}/expenses/{second['id']}/approve", headers=bearer(head), json={}
    )
    assert over.status_code == 409
    assert over.json()["code"] == "over_budget"
    ok = await client.post(
        f"{FIN}/expenses/{second['id']}/approve",
        headers=bearer(head),
        json={"over_budget_note": "Generator repairs pushed the bill up"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["over_budget_note"] == "Generator repairs pushed the bill up"
    report = (await client.get(f"{FIN}/budgets", headers=bearer(head))).json()
    [line] = [x for x in report["lines"] if x["category_id"] == utilities]
    assert line["over_budget"] is True
    assert line["remaining_minor"] == -1_000_000
    assert line["percent_used"] == 110

    # Reversing an approved expense posts the opposite entry; only approvers may.
    denied = await client.post(
        f"{FIN}/expenses/{second['id']}/void", headers=bearer(bursar), json={"reason": "Duplicate"}
    )
    assert denied.status_code == 403
    voided = await client.post(
        f"{FIN}/expenses/{second['id']}/void", headers=bearer(head), json={"reason": "Duplicate"}
    )
    assert voided.json()["status"] == "void"
    assert (await _balanced(client, bursar))["expenses"] == 6_000_000

    # Rejection needs a reason, and a rejected expense never touches the books.
    third = (await _expense(client, bursar, utilities, 1_000, method="cash", reference=None)).json()
    rejected = await client.post(
        f"{FIN}/expenses/{third['id']}/reject", headers=bearer(head), json={"reason": "No receipt"}
    )
    assert rejected.json()["status"] == "rejected"
    again = await client.post(
        f"{FIN}/expenses/{third['id']}/approve", headers=bearer(head), json={}
    )
    assert again.status_code == 409

    listed = await client.get(
        f"{FIN}/expenses", headers=bearer(head), params={"status": "approved"}
    )
    assert [x["id"] for x in listed.json()["items"]] == [e1["id"]]

    trail = (
        await client.get(
            "/api/v1/audit-events", headers=bearer(head), params={"action": "finance.expense"}
        )
    ).json()["items"]
    assert {"finance.expense_recorded", "finance.expense_approved", "finance.expense_voided"} <= {
        e["action"] for e in trail
    }


async def test_receipts_categories_and_permissions(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _school(client, container, mailbox, "receipts")
    bursar, teacher = sc["bursar"], sc["teacher"]
    added = await client.post(
        f"{FIN}/expense-categories", headers=bearer(bursar), json={"name": "Boarding house"}
    )
    assert added.status_code == 201
    dup = await client.post(
        f"{FIN}/expense-categories", headers=bearer(bursar), json={"name": "boarding HOUSE"}
    )
    assert dup.status_code == 409
    category = added.json()["id"]

    expense = (await _expense(client, bursar, category, 250_000)).json()
    assert expense["has_receipt"] is False
    up = await client.put(
        f"{FIN}/expenses/{expense['id']}/receipt",
        headers=bearer(bursar),
        files={"file": ("bill.pdf", PDF, "application/pdf")},
    )
    assert up.status_code == 200, up.text
    assert up.json()["has_receipt"] is True
    got = await client.get(f"{FIN}/expenses/{expense['id']}/receipt", headers=bearer(bursar))
    assert got.status_code == 200
    assert got.content == PDF
    assert got.headers["content-disposition"].startswith("attachment;")
    bad = await client.put(
        f"{FIN}/expenses/{expense['id']}/receipt",
        headers=bearer(bursar),
        files={"file": ("x.exe", b"MZ" + b"\x00" * 300, "application/pdf")},
    )
    assert bad.status_code == 422

    # Retired categories keep their history but take no new spending.
    retired = await client.patch(
        f"{FIN}/expense-categories/{category}", headers=bearer(bursar), json={"active": False}
    )
    assert retired.json()["active"] is False
    blocked = await _expense(client, bursar, category, 100)
    assert blocked.status_code == 422
    assert (
        await client.get(f"{FIN}/expenses/{expense['id']}", headers=bearer(bursar))
    ).status_code == 200

    # Teachers see none of it.
    assert (await client.get(f"{FIN}/expenses", headers=bearer(teacher))).status_code == 403
    assert (await client.get(f"{FIN}/budgets", headers=bearer(teacher))).status_code == 403

    # The recorder can cancel their own pending expense.
    cancel = await client.post(
        f"{FIN}/expenses/{expense['id']}/void",
        headers=bearer(bursar),
        json={"reason": "Wrong school"},
    )
    assert cancel.json()["status"] == "void"
