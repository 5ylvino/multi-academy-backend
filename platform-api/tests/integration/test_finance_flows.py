"""School fees over HTTP: catalog, billing, payments, credit, voids, refunds and the ledger."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import httpx
import pytest

from masms.infrastructure.container import Container
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer, register_and_verify

pytestmark = pytest.mark.integration

PASSWORD = "Member-Passphrase-2026"
ACA = "/api/v1/academics"
FIN = "/api/v1/finance"


def _today() -> date:
    return datetime.now(ZoneInfo("Africa/Lagos")).date()


def _key(n: int) -> dict[str, str]:
    return {"Idempotency-Key": f"pay-{n:04d}-abcdef"}


async def _member(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    director: dict[str, Any],
    *,
    slug: str,
    email: str,
    role: str,
    name: str,
) -> dict[str, Any]:
    r = await client.post(
        "/api/v1/users",
        headers=bearer(director),
        json={"email": email, "full_name": name, "roles": [role]},
    )
    assert r.status_code == 201, r.text
    await container.outbox.run_once()
    accepted = await client.post(
        f"/api/v1/schools/{slug}/invites/accept",
        json={"token": mailbox.last_invite_token(email), "password": PASSWORD},
    )
    assert accepted.status_code == 200, accepted.text
    body: dict[str, Any] = accepted.json()
    return body


async def _school(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, slug: str
) -> dict[str, Any]:
    """Current term, two classes (primary and secondary), a bursar and three students."""
    d = await register_and_verify(client, container, mailbox, slug=slug, email=f"h@{slug}.ng")
    h = bearer(d)
    today = _today()
    s = await client.post(
        f"{ACA}/sessions",
        headers=h,
        json={
            "name": "This session",
            "starts_on": str(today - timedelta(days=90)),
            "ends_on": str(today + timedelta(days=200)),
        },
    )
    assert s.status_code == 201, s.text
    t = await client.post(
        f"{ACA}/sessions/{s.json()['id']}/terms",
        headers=h,
        json={
            "code": "first",
            "starts_on": str(today - timedelta(days=30)),
            "ends_on": str(today + timedelta(days=60)),
        },
    )
    assert t.status_code == 201, t.text
    assert (
        await client.post(f"{ACA}/terms/{t.json()['id']}/current", headers=h)
    ).status_code == 200

    people = {}
    for key, role, name in (
        ("bursar", "bursar", "Bisi Bursar"),
        ("teacher", "class_teacher", "Tunde Teacher"),
        ("s1", "student", "Ada Obi"),
        ("s2", "student", "Bola Ade"),
        ("s3", "student", "Chidi Okeke"),
    ):
        people[key] = await _member(
            client, container, mailbox, d, slug=slug, email=f"{key}@{slug}.ng", role=role, name=name
        )
    classes = {}
    for code, level in (("P3", "primary"), ("JSS1", "secondary")):
        k = await client.post(
            f"{ACA}/classes",
            headers=h,
            json={"name": code, "code": code, "school_level": level},
        )
        assert k.status_code == 201, k.text
        classes[code] = k.json()["id"]
    for student, code in (("s1", "P3"), ("s2", "P3"), ("s3", "JSS1")):
        r = await client.post(
            f"{ACA}/enrolments",
            headers=h,
            json={"student_id": people[student]["user_id"], "class_id": classes[code]},
        )
        assert r.status_code == 201, r.text
    return {
        "director": d,
        **people,
        "classes": classes,
        "session_id": s.json()["id"],
        "term_id": t.json()["id"],
    }


async def _fee(client: httpx.AsyncClient, who: dict[str, Any], **body: Any) -> dict[str, Any]:
    r = await client.post(f"{FIN}/fee-items", headers=bearer(who), json=body)
    assert r.status_code == 201, r.text
    result: dict[str, Any] = r.json()
    return result


async def _statement(
    client: httpx.AsyncClient, who: dict[str, Any], student: dict[str, Any]
) -> dict[str, Any]:
    r = await client.get(f"{FIN}/students/{student['user_id']}/statement", headers=bearer(who))
    assert r.status_code == 200, r.text
    result: dict[str, Any] = r.json()
    return result


async def _balanced(client: httpx.AsyncClient, who: dict[str, Any]) -> dict[str, int]:
    r = await client.get(f"{FIN}/ledger/trial-balance", headers=bearer(who))
    assert r.status_code == 200, r.text
    assert r.json()["balanced"] is True
    return {row["account"]: row["balance_minor"] for row in r.json()["rows"]}


async def _post_unbalanced(conn: asyncpg.Connection, tenant_id: str) -> None:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
        entry = await conn.fetchval(
            """
            INSERT INTO finance_journal_entries
              (tenant_id, id, kind, source_type, source_id, memo, posted_at)
            VALUES ($1::uuid, gen_random_uuid(), 'x', 'x', gen_random_uuid(), 'x', now())
            RETURNING id
            """,
            tenant_id,
        )
        await conn.execute(
            """
            INSERT INTO finance_journal_lines
              (tenant_id, entry_id, line_no, account, amount_minor, currency)
            VALUES ($1::uuid, $2, 1, 'cash', 100, 'NGN')
            """,
            tenant_id,
            entry,
        )


async def test_term_billing_payments_credit_and_refunds(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _school(client, container, mailbox, "fees")
    b, d = sc["bursar"], sc["director"]

    settings = await client.get(f"{FIN}/settings", headers=bearer(b))
    assert settings.json() == {"currency": "NGN", "default_due_days": 14, "currency_locked": False}

    await _fee(client, b, name="Tuition", amount_minor=10_000_000, school_level="primary")
    await _fee(client, b, name="Senior tuition", amount_minor=15_000_000, school_level="secondary")
    await _fee(client, b, name="Development levy", amount_minor=2_000_000, terms=["first"])
    await _fee(client, b, name="Levy next term", amount_minor=1_000_000, terms=["second"])
    bus = await _fee(client, b, name="School bus", amount_minor=3_000_000, mandatory=False)
    wrong_currency = await client.post(
        f"{FIN}/fee-items",
        headers=bearer(b),
        json={"name": "Dollar fee", "amount_minor": 100, "currency": "USD"},
    )
    assert wrong_currency.status_code == 422

    grant = await client.post(
        f"{FIN}/scholarships",
        headers=bearer(b),
        json={
            "student_id": sc["s2"]["user_id"],
            "name": "Sibling",
            "kind": "percent",
            "value": 1000,
        },
    )
    assert grant.status_code == 201, grant.text

    run = await client.post(f"{FIN}/billing-runs", headers=bearer(b), json={})
    assert run.status_code == 200, run.text
    assert run.json()["invoiced"] == 3
    # s1: 100k + 20k; s2: 120k less 10%; s3: 150k + 20k  (in naira; amounts are kobo)
    assert run.json()["total_billed"] == {
        "amount_minor": 12_000_000 + 10_800_000 + 17_000_000,
        "currency": "NGN",
    }
    again = await client.post(f"{FIN}/billing-runs", headers=bearer(b), json={})
    assert again.json()["invoiced"] == 0
    assert again.json()["already_billed"] == 3

    s1_invoices = await client.get(
        f"{FIN}/invoices", headers=bearer(b), params={"student_id": sc["s1"]["user_id"]}
    )
    (term_invoice,) = s1_invoices.json()["items"]
    assert term_invoice["number"].startswith("INV-")
    assert [x["description"] for x in term_invoice["lines"]] == ["Development levy", "Tuition"]
    assert term_invoice["status"] == "issued"
    s2_inv = (
        await client.get(
            f"{FIN}/invoices", headers=bearer(b), params={"student_id": sc["s2"]["user_id"]}
        )
    ).json()["items"][0]
    assert s2_inv["discount_total"]["amount_minor"] == 1_200_000

    locked = await client.put(
        f"{FIN}/settings", headers=bearer(b), json={"currency": "USD", "default_due_days": 7}
    )
    assert locked.status_code == 422
    assert locked.json()["code"] == "currency_locked"

    # Students see only their own fees; teachers can't record money.
    own = await client.get(f"{FIN}/invoices", headers=bearer(sc["s1"]))
    assert [i["id"] for i in own.json()["items"]] == [term_invoice["id"]]
    peek = await client.get(f"{FIN}/invoices/{s2_inv['id']}", headers=bearer(sc["s1"]))
    assert peek.status_code == 404
    other = await client.get(
        f"{FIN}/students/{sc['s2']['user_id']}/statement", headers=bearer(sc["s1"])
    )
    assert other.status_code == 403
    teacher_pay = await client.post(
        f"{FIN}/payments",
        headers={**bearer(sc["teacher"]), **_key(1)},
        json={"student_id": sc["s1"]["user_id"], "amount_minor": 100, "method": "cash"},
    )
    assert teacher_pay.status_code == 403

    # Part payment, idempotent replay, key reuse with another body, missing key.
    body = {"student_id": sc["s1"]["user_id"], "amount_minor": 5_000_000, "method": "cash"}
    paid = await client.post(f"{FIN}/payments", headers={**bearer(b), **_key(2)}, json=body)
    assert paid.status_code == 201, paid.text
    assert paid.json()["receipt_number"].startswith("RCT-")
    assert paid.json()["allocations"] == [
        {"invoice_id": term_invoice["id"], "amount": {"amount_minor": 5_000_000, "currency": "NGN"}}
    ]
    replay = await client.post(f"{FIN}/payments", headers={**bearer(b), **_key(2)}, json=body)
    assert replay.status_code == 200
    assert replay.headers["idempotent-replayed"] == "true"
    assert replay.json()["id"] == paid.json()["id"]
    clash = await client.post(
        f"{FIN}/payments",
        headers={**bearer(b), **_key(2)},
        json={**body, "amount_minor": 1},
    )
    assert clash.status_code == 409
    assert clash.json()["code"] == "idempotency_key_reused"
    no_key = await client.post(f"{FIN}/payments", headers=bearer(b), json=body)
    assert no_key.status_code == 422
    assert no_key.json()["code"] == "idempotency_key_required"
    no_ref = await client.post(
        f"{FIN}/payments",
        headers={**bearer(b), **_key(3)},
        json={**body, "method": "bank_transfer"},
    )
    assert no_ref.status_code == 422

    # Overpay: the invoice is settled and the rest is held as credit.
    over = await client.post(
        f"{FIN}/payments",
        headers={**bearer(b), **_key(4)},
        json={**body, "amount_minor": 10_000_000, "method": "pos"},
    )
    assert over.status_code == 201, over.text
    assert over.json()["unused_credit"]["amount_minor"] == 3_000_000
    invoice = (await client.get(f"{FIN}/invoices/{term_invoice['id']}", headers=bearer(b))).json()
    assert invoice["status"] == "paid"
    assert invoice["balance"]["amount_minor"] == 0
    stmt = await _statement(client, sc["s1"], sc["s1"])
    assert stmt["credit"]["amount_minor"] == 3_000_000
    assert stmt["net_due"]["amount_minor"] == -3_000_000
    assert stmt["lines"][-1]["balance_minor"] == -3_000_000

    # A one-off invoice for an optional item is paid from credit straight away.
    extra = await client.post(
        f"{FIN}/invoices",
        headers=bearer(b),
        json={"student_id": sc["s1"]["user_id"], "fee_item_ids": [bus["id"]]},
    )
    assert extra.status_code == 201, extra.text
    assert extra.json()["status"] == "paid"
    assert (await _statement(client, b, sc["s1"]))["net_due"]["amount_minor"] == 0

    # Voiding it gives the money back as credit.
    void = await client.post(
        f"{FIN}/invoices/{extra.json()['id']}/void",
        headers=bearer(b),
        json={"reason": "Student does not use the bus"},
    )
    assert void.status_code == 200, void.text
    assert void.json()["status"] == "void"
    assert (await _statement(client, b, sc["s1"]))["credit"]["amount_minor"] == 3_000_000

    # Refund the credit: the bursar asks, the bursar can't approve, the director does.
    refund = await client.post(
        f"{FIN}/payments/{over.json()['id']}/refunds",
        headers=bearer(b),
        json={"amount_minor": 3_000_000, "method": "bank_transfer", "reason": "Overpaid fees"},
    )
    assert refund.status_code == 201, refund.text
    assert refund.json()["status"] == "pending_approval"
    too_much = await client.post(
        f"{FIN}/payments/{over.json()['id']}/refunds",
        headers=bearer(b),
        json={"amount_minor": 1, "method": "cash", "reason": "Again"},
    )
    assert too_much.status_code == 422
    assert too_much.json()["code"] == "insufficient_credit"
    self_approve = await client.post(
        f"{FIN}/refunds/{refund.json()['id']}/approve", headers=bearer(b), json={}
    )
    assert self_approve.status_code == 403
    assert self_approve.json()["code"] == "dual_control_required"
    pending = await client.get(
        f"{FIN}/refunds", headers=bearer(d), params={"status": "pending_approval"}
    )
    assert [r["id"] for r in pending.json()] == [refund.json()["id"]]
    approved = await client.post(
        f"{FIN}/refunds/{refund.json()['id']}/approve", headers=bearer(d), json={"note": "OK"}
    )
    assert approved.status_code == 200, approved.text
    final = await _statement(client, b, sc["s1"])
    assert final["credit"]["amount_minor"] == 0
    assert final["net_due"]["amount_minor"] == 0
    assert final["refunded"]["amount_minor"] == 3_000_000

    books = await _balanced(client, b)
    assert books["cash"] == 5_000_000
    assert books["bank"] == 10_000_000 - 3_000_000
    assert books["discounts"] == 1_200_000


async def test_reversal_reopens_invoices_and_the_ledger_is_append_only(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, app_dsn: str
) -> None:
    sc = await _school(client, container, mailbox, "revs")
    b = sc["bursar"]
    await _fee(client, b, name="Tuition", amount_minor=1_000_000)
    assert (await client.post(f"{FIN}/billing-runs", headers=bearer(b), json={})).status_code == 200

    cheque = await client.post(
        f"{FIN}/payments",
        headers={**bearer(b), **_key(10)},
        json={
            "student_id": sc["s3"]["user_id"],
            "amount_minor": 1_000_000,
            "method": "cheque",
            "reference": "CHQ 000123",
        },
    )
    assert cheque.status_code == 201, cheque.text
    inv_id = cheque.json()["allocations"][0]["invoice_id"]
    teacher = await client.post(
        f"{FIN}/payments/{cheque.json()['id']}/reverse",
        headers=bearer(sc["teacher"]),
        json={"reason": "Cheque bounced"},
    )
    assert teacher.status_code == 403
    reversed_ = await client.post(
        f"{FIN}/payments/{cheque.json()['id']}/reverse",
        headers=bearer(b),
        json={"reason": "Cheque bounced"},
    )
    assert reversed_.status_code == 200, reversed_.text
    assert reversed_.json()["status"] == "reversed"
    invoice = (await client.get(f"{FIN}/invoices/{inv_id}", headers=bearer(b))).json()
    assert invoice["status"] == "issued"
    assert invoice["balance"]["amount_minor"] == 1_000_000
    again = await client.post(
        f"{FIN}/payments/{cheque.json()['id']}/reverse",
        headers=bearer(b),
        json={"reason": "Cheque bounced"},
    )
    assert again.status_code == 409
    stmt = await _statement(client, b, sc["s3"])
    assert stmt["net_due"]["amount_minor"] == 1_000_000
    assert [line["kind"] for line in stmt["lines"]] == ["invoice", "payment", "payment_reversal"]
    books = await _balanced(client, b)
    assert books.get("bank", 0) == 0

    # The app role can't rewrite history, and the database refuses unbalanced entries.
    conn = await asyncpg.connect(app_dsn)
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", sc["director"]["tenant_id"]
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute("UPDATE finance_journal_lines SET amount_minor = 1")
        with pytest.raises(asyncpg.CheckViolationError):
            await _post_unbalanced(conn, sc["director"]["tenant_id"])
    finally:
        await conn.close()


async def test_finance_data_stays_inside_its_school(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    one = await _school(client, container, mailbox, "onefee")
    two = await register_and_verify(client, container, mailbox, slug="twofee", email="h@twofee.ng")
    await _fee(client, one["bursar"], name="Tuition", amount_minor=500_000)
    await client.post(f"{FIN}/billing-runs", headers=bearer(one["bursar"]), json={})
    inv = (await client.get(f"{FIN}/invoices", headers=bearer(one["bursar"]))).json()["items"][0]

    assert (await client.get(f"{FIN}/invoices/{inv['id']}", headers=bearer(two))).status_code == 404
    assert (await client.get(f"{FIN}/invoices", headers=bearer(two))).json()["items"] == []
    assert (await client.get(f"{FIN}/fee-items", headers=bearer(two))).json() == []
    pay = await client.post(
        f"{FIN}/payments",
        headers={**bearer(two), **_key(20)},
        json={"student_id": one["s1"]["user_id"], "amount_minor": 100, "method": "cash"},
    )
    assert pay.status_code == 404


async def test_concurrent_payments_never_over_apply(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _school(client, container, mailbox, "race")
    b = sc["bursar"]
    await _fee(client, b, name="Tuition", amount_minor=1_000_000)
    await client.post(f"{FIN}/billing-runs", headers=bearer(b), json={})

    async def pay(n: int) -> httpx.Response:
        return await client.post(
            f"{FIN}/payments",
            headers={**bearer(b), **_key(100 + n)},
            json={"student_id": sc["s1"]["user_id"], "amount_minor": 600_000, "method": "cash"},
        )

    results = await asyncio.gather(*(pay(n) for n in range(4)))
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    applied = sum(r.json()["allocated"]["amount_minor"] for r in results)
    assert applied == 1_000_000
    stmt = await _statement(client, b, sc["s1"])
    assert stmt["owed"]["amount_minor"] == 0
    assert stmt["credit"]["amount_minor"] == 4 * 600_000 - 1_000_000
    receipts = {r.json()["receipt_number"] for r in results}
    assert len(receipts) == 4
    await _balanced(client, b)
