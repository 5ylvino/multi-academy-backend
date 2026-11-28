"""Paying school fees online through Paystack, end to end over HTTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx
import pytest

from app.infrastructure.container import Container
from tests.fakes.paystack import GOOD_PUBLIC, GOOD_SECRET, FakePaystack
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer
from tests.integration.test_finance_flows import FIN, _fee, _member, _school, _statement
from tests.integration.test_payment_proofs_flows import _parent

pytestmark = pytest.mark.integration

TUITION = 10_000_000


async def _ready(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, slug: str
) -> dict[str, Any]:
    """A billed school with a linked parent, Paystack connected and online payments on."""
    sc = await _school(client, container, mailbox, slug)
    b = sc["bursar"]
    sc["parent"] = await _parent(client, container, mailbox, sc, slug)
    await _fee(client, b, name="Tuition", amount_minor=TUITION)
    assert (await client.post(f"{FIN}/billing-runs", headers=bearer(b), json={})).status_code == 200
    connected = await client.put(
        f"{FIN}/payment-gateway",
        headers=bearer(b),
        json={"provider": "paystack", "public_key": GOOD_PUBLIC, "secret_key": GOOD_SECRET},
    )
    assert connected.status_code == 200, connected.text
    sc["gateway"] = connected.json()
    on = await client.put(
        f"{FIN}/settings",
        headers=bearer(b),
        json={"currency": "NGN", "default_due_days": 14, "online_payments_enabled": True},
    )
    assert on.status_code == 200, on.text
    return sc


async def _start(
    client: httpx.AsyncClient, who: dict[str, Any], student: dict[str, Any], **body: Any
) -> httpx.Response:
    return await client.post(
        f"{FIN}/checkouts",
        headers=bearer(who),
        json={"student_id": student["user_id"], **body},
    )


async def _webhook(
    client: httpx.AsyncClient, sc: dict[str, Any], body: bytes, signature: str
) -> httpx.Response:
    url = sc["gateway"]["webhook_url"].split("/api/v1", 1)[1]
    return await client.post(
        f"/api/v1{url}",
        content=body,
        headers={"x-paystack-signature": signature, "content-type": "application/json"},
    )


async def test_parent_pays_online_and_invoice_is_settled(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, paystack: FakePaystack
) -> None:
    sc = await _ready(client, container, mailbox, "paysmooth")
    b, parent = sc["bursar"], sc["parent"]
    assert sc["gateway"]["mode"] == "test"
    assert sc["gateway"]["key_hint"] == GOOD_SECRET[-4:]
    assert "secret_key" not in sc["gateway"]
    status = (await client.get(f"{FIN}/payment-gateway", headers=bearer(b))).json()
    assert status["connected"] is True
    assert status["providers"] == ["paystack"]

    started = await _start(client, parent, sc["s1"])
    assert started.status_code == 201, started.text
    checkout = started.json()
    assert checkout["status"] == "pending"
    assert checkout["amount_minor"] == TUITION  # defaults to everything owed
    assert checkout["authorization_url"].startswith("https://checkout.paystack.com/")
    assert paystack.txns[checkout["reference"]].email == "parent@paysmooth.ng"

    # A double-click returns the same payment page.
    again = await _start(client, parent, sc["s1"])
    assert again.json()["id"] == checkout["id"]

    paystack.pay(checkout["reference"])
    body, signature = paystack.webhook(checkout["reference"])
    hook = await _webhook(client, sc, body, signature)
    assert hook.status_code == 200, hook.text
    assert hook.json() == {"received": True, "matched": True}

    done = (await client.get(f"{FIN}/checkouts/{checkout['id']}", headers=bearer(parent))).json()
    assert done["status"] == "succeeded"
    assert done["paid_minor"] == TUITION
    assert done["authorization_url"] is None
    stmt = await _statement(client, parent, sc["s1"])
    assert stmt["paid"]["amount_minor"] == TUITION
    assert stmt["net_due"]["amount_minor"] == 0

    # Provider retries and the parent's return page never record it twice.
    assert (await _webhook(client, sc, body, signature)).status_code == 200
    confirmed = await client.post(
        f"{FIN}/checkouts/{checkout['id']}/confirm", headers=bearer(parent)
    )
    assert confirmed.json()["status"] == "succeeded"
    payments = (await client.get(f"{FIN}/payments", headers=bearer(b))).json()["items"]
    online = [p for p in payments if p["method"] == "online"]
    assert len(online) == 1
    assert online[0]["reference"] == checkout["reference"]

    # Nothing left to pay.
    nothing = await _start(client, parent, sc["s1"])
    assert nothing.status_code == 422
    assert nothing.json()["code"] == "nothing_due"

    trail = (
        await client.get(
            "/api/v1/audit-events", headers=bearer(sc["director"]), params={"action": "finance."}
        )
    ).json()["items"]
    actions = {e["action"] for e in trail}
    assert {
        "finance.gateway_connected",
        "finance.checkout_started",
        "finance.online_payment_received",
    } <= actions
    received = next(e for e in trail if e["action"] == "finance.online_payment_received")
    assert received["actor_name"] == "Ngozi Obi"
    assert GOOD_SECRET not in str(trail)


async def test_return_from_payment_page_confirms_without_webhook(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, paystack: FakePaystack
) -> None:
    sc = await _ready(client, container, mailbox, "payreturn")
    parent = sc["parent"]
    checkout = (await _start(client, parent, sc["s1"], amount_minor=4_000_000)).json()
    assert checkout["amount_minor"] == 4_000_000

    still = await client.post(f"{FIN}/checkouts/{checkout['id']}/confirm", headers=bearer(parent))
    assert still.json()["status"] == "pending"
    paystack.pay(checkout["reference"])
    paid = await client.post(f"{FIN}/checkouts/{checkout['id']}/confirm", headers=bearer(parent))
    assert paid.json()["status"] == "succeeded"
    stmt = await _statement(client, parent, sc["s1"])
    assert stmt["net_due"]["amount_minor"] == TUITION - 4_000_000

    mine = (await client.get(f"{FIN}/checkouts", headers=bearer(parent))).json()["items"]
    assert [c["id"] for c in mine] == [checkout["id"]]


async def test_who_can_pay_and_how_much(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _ready(client, container, mailbox, "paylimits")
    parent = sc["parent"]
    over = await _start(client, parent, sc["s1"], amount_minor=TUITION + 1)
    assert over.status_code == 422
    assert over.json()["code"] == "amount_over_balance"
    # Not their child.
    assert (await _start(client, parent, sc["s2"])).status_code == 403
    # Another parent can't see this family's checkout.
    mine = (await _start(client, parent, sc["s1"])).json()
    stranger = await _member(
        client,
        container,
        mailbox,
        sc["director"],
        slug="paylimits",
        email="other@paylimits.ng",
        role="parent",
        name="Other Parent",
    )
    hidden = await client.get(f"{FIN}/checkouts/{mine['id']}", headers=bearer(stranger))
    assert hidden.status_code == 404
    # Students are usually minors: paying is for parents (and staff record other payments).
    assert (await _start(client, sc["s1"], sc["s1"])).status_code == 403


async def test_online_payments_must_be_switched_on(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, paystack: FakePaystack
) -> None:
    sc = await _school(client, container, mailbox, "payoff")
    b = sc["bursar"]
    parent = await _parent(client, container, mailbox, sc, "payoff")
    await _fee(client, b, name="Tuition", amount_minor=TUITION)
    await client.post(f"{FIN}/billing-runs", headers=bearer(b), json={})
    off = await _start(client, parent, sc["s1"])
    assert off.status_code == 403
    assert off.json()["code"] == "online_payments_off"

    bad_key = await client.put(
        f"{FIN}/payment-gateway",
        headers=bearer(b),
        json={"public_key": GOOD_PUBLIC, "secret_key": "sk_test_" + "0" * 40},
    )
    assert bad_key.status_code == 422
    assert "didn't accept" in bad_key.json()["title"]
    malformed = await client.put(
        f"{FIN}/payment-gateway",
        headers=bearer(b),
        json={"public_key": GOOD_PUBLIC, "secret_key": "not-a-key-at-all"},
    )
    assert malformed.status_code == 422
    # Parents can't connect accounts.
    assert (
        await client.put(
            f"{FIN}/payment-gateway",
            headers=bearer(parent),
            json={"public_key": GOOD_PUBLIC, "secret_key": GOOD_SECRET},
        )
    ).status_code == 403
    assert paystack.calls.count("GET /transaction") == 1


async def test_forged_webhook_is_refused(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, paystack: FakePaystack
) -> None:
    sc = await _ready(client, container, mailbox, "payforge")
    checkout = (await _start(client, sc["parent"], sc["s1"])).json()
    body, _ = paystack.webhook(checkout["reference"])
    forged = await _webhook(client, sc, body, "0" * 128)
    assert forged.status_code == 401
    # Even a correctly signed event is checked with Paystack: unpaid stays unpaid.
    body, signature = paystack.webhook(checkout["reference"])
    assert (await _webhook(client, sc, body, signature)).status_code == 200
    got = await client.get(f"{FIN}/checkouts/{checkout['id']}", headers=bearer(sc["parent"]))
    assert got.json()["status"] == "pending"
    # Other events and other people's references are acknowledged and ignored.
    body, signature = paystack.webhook("NOT-OURS")
    ignored = await _webhook(client, sc, body, signature)
    assert ignored.json() == {"received": True, "matched": False}


async def test_provider_outage_is_clear_and_recoverable(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, paystack: FakePaystack
) -> None:
    sc = await _ready(client, container, mailbox, "paydown")
    paystack.down = True
    failed = await _start(client, sc["parent"], sc["s1"])
    assert failed.status_code == 502
    assert failed.json()["code"] == "provider_unavailable"
    paystack.down = False
    retry = await _start(client, sc["parent"], sc["s1"])
    assert retry.status_code == 201
    assert retry.json()["status"] == "pending"
    history = (await client.get(f"{FIN}/checkouts", headers=bearer(sc["bursar"]))).json()["items"]
    assert sorted(c["status"] for c in history) == ["failed", "pending"]


async def _age(owner_dsn: str, checkout_id: str, *, minutes: int, expired: bool = False) -> None:
    conn = await asyncpg.connect(owner_dsn)
    try:
        await conn.execute("SET app.rls_bypass = 'on'")
        then = datetime.now(UTC) - timedelta(minutes=minutes)
        await conn.execute(
            "UPDATE finance_checkouts SET created_at = $2, expires_at = $3 WHERE id = $1",
            __import__("uuid").UUID(checkout_id),
            then,
            then + timedelta(minutes=1) if expired else then + timedelta(hours=24),
        )
    finally:
        await conn.close()


async def test_reconciliation_catches_missed_webhooks_and_closes_unpaid(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    paystack: FakePaystack,
    owner_dsn: str,
) -> None:
    sc = await _ready(client, container, mailbox, "payrecon")
    parent = sc["parent"]
    paid = (await _start(client, parent, sc["s1"], amount_minor=1_000_000)).json()
    declined = (await _start(client, parent, sc["s1"], amount_minor=2_000_000)).json()
    unpaid = (await _start(client, parent, sc["s1"], amount_minor=3_000_000)).json()
    fresh = (await _start(client, parent, sc["s1"], amount_minor=4_000_000)).json()
    paystack.pay(paid["reference"])
    paystack.decline(declined["reference"])
    for c in (paid, declined):
        await _age(owner_dsn, c["id"], minutes=10)
    await _age(owner_dsn, unpaid["id"], minutes=60 * 25, expired=True)

    report = await container.use_cases.online_payments.reconcile()
    assert (report.checked, report.succeeded, report.failed, report.abandoned) == (3, 1, 1, 1)
    h = bearer(parent)
    status = {
        c["id"]: c["status"]
        for c in (await client.get(f"{FIN}/checkouts", headers=h)).json()["items"]
    }
    assert status == {
        paid["id"]: "succeeded",
        declined["id"]: "failed",
        unpaid["id"]: "abandoned",
        fresh["id"]: "pending",
    }
    # A payment that lands after the checkout was closed still counts.
    paystack.pay(unpaid["reference"])
    body, signature = paystack.webhook(unpaid["reference"])
    assert (await _webhook(client, sc, body, signature)).json()["matched"] is True
    late = (await client.get(f"{FIN}/checkouts/{unpaid['id']}", headers=h)).json()
    assert late["status"] == "succeeded"
    stmt = await _statement(client, parent, sc["s1"])
    assert stmt["paid"]["amount_minor"] == 4_000_000


async def test_wrong_currency_is_held_for_review(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, paystack: FakePaystack
) -> None:
    sc = await _ready(client, container, mailbox, "payfx")
    checkout = (await _start(client, sc["parent"], sc["s1"])).json()
    paystack.pay(checkout["reference"], currency="USD")
    body, signature = paystack.webhook(checkout["reference"])
    await _webhook(client, sc, body, signature)
    got = await client.get(f"{FIN}/checkouts/{checkout['id']}", headers=bearer(sc["bursar"]))
    assert got.json()["status"] == "needs_review"
    assert (await _statement(client, sc["bursar"], sc["s1"]))["paid"]["amount_minor"] == 0


async def test_disconnecting_stops_new_payments_but_finishes_old_ones(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, paystack: FakePaystack
) -> None:
    sc = await _ready(client, container, mailbox, "payunplug")
    b, parent = sc["bursar"], sc["parent"]
    checkout = (await _start(client, parent, sc["s1"])).json()
    assert (await client.delete(f"{FIN}/payment-gateway", headers=bearer(b))).status_code == 204
    settings = (await client.get(f"{FIN}/settings", headers=bearer(b))).json()
    assert settings["online_payments_enabled"] is False
    blocked = await _start(client, parent, sc["s1"], amount_minor=1_000)
    assert blocked.json()["code"] == "online_payments_off"

    paystack.pay(checkout["reference"])
    body, signature = paystack.webhook(checkout["reference"])
    assert (await _webhook(client, sc, body, signature)).json()["matched"] is True
    got = (await client.get(f"{FIN}/checkouts/{checkout['id']}", headers=bearer(parent))).json()
    assert got["status"] == "succeeded"
