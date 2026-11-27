"""Schools paying the platform for their plan, end to end over HTTP, with a controllable clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from app.domain.subscriptions.billing import GRACE_PERIOD
from app.infrastructure.config.settings import Settings
from app.infrastructure.container import Container
from app.use_cases.shared.ports import EmailMessage
from app.use_cases.subscriptions.notifications import SendPlatformInvoiceEmail
from tests.fakes.paystack import GOOD_PUBLIC, GOOD_SECRET, FakePaystack
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer, platform_session
from tests.integration.test_finance_flows import _school

pytestmark = pytest.mark.integration

BASE = 5_000_000  # NGN 50,000.00 per term
PER_STUDENT = 250_000  # NGN 2,500.00 per student per term
BANK = "GTBank 0123456789, MA-SMS Ltd"
SUB = "/api/v1/subscription"
CONSOLE = "/api/v1/platform/billing"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "platform_paystack_public_key": GOOD_PUBLIC,
            "platform_paystack_secret_key": SecretStr(GOOD_SECRET),
            "platform_bank_transfer_details": BANK,
        }
    )


class Clock:
    def __init__(self) -> None:
        self.at = datetime.now(UTC)

    def now(self) -> datetime:
        return self.at


@pytest.fixture
def clock(container: Container) -> Clock:
    fake = Clock()
    container.use_cases.platform_billing._clock = fake
    return fake


async def _paid_school(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    slug: str,
    owner: dict[str, Any],
    *,
    plan: str = "standard",
) -> dict[str, Any]:
    sc = await _school(client, container, mailbox, slug)
    sc["id"] = sc["director"]["tenant_id"]
    moved = await client.put(
        f"{CONSOLE}/schools/{sc['id']}/plan", headers=bearer(owner), json={"plan_code": plan}
    )
    assert moved.status_code == 200, moved.text
    return sc


async def _plans(client: httpx.AsyncClient, owner: dict[str, Any]) -> None:
    for code, base, per in (("standard", BASE, PER_STUDENT), ("perhead", 0, PER_STUDENT)):
        r = await client.put(
            f"/api/v1/platform/plans/{code}",
            headers=bearer(owner),
            json={
                "name": code.title(),
                "base_price_minor": base,
                "price_per_student_minor": per,
                "billing_interval_months": 4,
            },
        )
        assert r.status_code == 200, r.text


async def _run(client: httpx.AsyncClient, owner: dict[str, Any]) -> dict[str, Any]:
    r = await client.post(f"{CONSOLE}/run", headers=bearer(owner))
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    assert body["errors"] == 0, body
    return body


async def _open_invoice(client: httpx.AsyncClient, sc: dict[str, Any]) -> dict[str, Any]:
    sub = (await client.get(SUB, headers=bearer(sc["director"]))).json()
    assert len(sub["open_invoices"]) == 1, sub
    invoice: dict[str, Any] = sub["open_invoices"][0]
    return invoice


async def _hook(client: httpx.AsyncClient, paystack: FakePaystack, ref: str) -> httpx.Response:
    body, signature = paystack.webhook(ref)
    return await client.post(
        "/api/v1/webhooks/platform-billing/paystack",
        content=body,
        headers={"x-paystack-signature": signature, "content-type": "application/json"},
    )


def _ref(paystack: FakePaystack, url: str) -> str:
    return next(r for r in paystack.txns if url.endswith(r.lower()))


async def test_trial_school_is_invoiced_pays_online_and_becomes_active(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    paystack: FakePaystack,
    clock: Clock,
) -> None:
    owner = await platform_session(client, container, ["owner"])
    await _plans(client, owner)
    sc = await _paid_school(client, container, mailbox, "billme", owner)

    sub = (await client.get(SUB, headers=bearer(sc["director"]))).json()
    assert sub["status"] == "trialing"
    assert sub["plan_code"] == "standard"
    assert sub["active_students"] == 3
    assert sub["estimated_next_minor"] == BASE + 3 * PER_STUDENT
    assert sub["open_invoices"] == []
    assert sub["pay_online"] is True
    assert sub["bank_transfer_details"] == BANK
    trial_end = datetime.fromisoformat(sub["trial_ends_at"])
    assert datetime.fromisoformat(sub["current_period_end"]) == trial_end

    # Nothing is billed while the trial has weeks left.
    assert (await _run(client, owner))["invoices_issued"] == 0

    # A week before the trial ends, the first term's invoice goes out, due on the last day.
    clock.at = trial_end - timedelta(days=6)
    assert (await _run(client, owner))["invoices_issued"] == 1
    assert (await _run(client, owner))["invoices_issued"] == 0  # never twice
    invoice = await _open_invoice(client, sc)
    assert invoice["total_minor"] == BASE + 3 * PER_STUDENT
    assert invoice["student_count"] == 3
    assert datetime.fromisoformat(invoice["due_at"]) == trial_end
    assert datetime.fromisoformat(invoice["suspends_at"]) == trial_end + GRACE_PERIOD
    assert invoice["number"].startswith(f"MAS-{clock.at.year}-")
    await container.outbox.run_once()
    issued = [p for n, p in mailbox.events if n == "subscriptions.invoice_issued"]
    assert len(issued) == 1
    assert issued[0]["number"] == invoice["number"]

    # The school pays online; a double-click gets the same page.
    paid = await client.post(f"{SUB}/invoices/{invoice['id']}/pay", headers=bearer(sc["director"]))
    assert paid.status_code == 200, paid.text
    url = paid.json()["authorization_url"]
    assert url.startswith("https://checkout.paystack.com/")
    again = await client.post(f"{SUB}/invoices/{invoice['id']}/pay", headers=bearer(sc["director"]))
    assert again.json()["authorization_url"] == url
    ref = _ref(paystack, url)
    assert paystack.txns[ref].amount == BASE + 3 * PER_STUDENT
    assert paystack.txns[ref].email == "h@billme.ng"

    paystack.pay(ref)
    hook = await _hook(client, paystack, ref)
    assert hook.json() == {"received": True, "matched": True}
    assert (await _hook(client, paystack, ref)).status_code == 200  # retries are harmless

    sub = (await client.get(SUB, headers=bearer(sc["director"]))).json()
    assert sub["status"] == "active"
    assert sub["school_status"] == "active"
    assert sub["open_invoices"] == []
    assert datetime.fromisoformat(sub["current_period_start"]) == trial_end
    history = (await client.get(f"{SUB}/invoices", headers=bearer(sc["director"]))).json()
    [done] = history["items"]
    assert done["status"] == "paid"
    assert done["settlement_method"] == "online"
    assert done["authorization_url"] is None

    await container.outbox.run_once()
    assert any(n == "subscriptions.invoice_paid" for n, _ in mailbox.events)
    trail = (
        await client.get(
            "/api/v1/audit-events",
            headers=bearer(sc["director"]),
            params={"action": "subscription."},
        )
    ).json()["items"]
    actions = {e["action"] for e in trail}
    assert {
        "subscription.started",
        "subscription.invoice_issued",
        "subscription.checkout_started",
        "subscription.invoice_paid",
    } <= actions
    assert GOOD_SECRET not in str(trail)


async def test_unpaid_school_is_reminded_suspended_and_reinstated_by_pay_link(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    paystack: FakePaystack,
    clock: Clock,
) -> None:
    owner = await platform_session(client, container, ["owner"])
    await _plans(client, owner)
    sc = await _paid_school(client, container, mailbox, "latepay", owner)
    trial_end = datetime.fromisoformat(
        (await client.get(SUB, headers=bearer(sc["director"]))).json()["trial_ends_at"]
    )
    clock.at = trial_end - timedelta(days=3)
    await _run(client, owner)
    invoice = await _open_invoice(client, sc)

    clock.at = trial_end + timedelta(hours=2)
    assert (await _run(client, owner))["reminders_sent"] == 1
    assert (await _run(client, owner))["reminders_sent"] == 0
    sub = (await client.get(SUB, headers=bearer(sc["director"]))).json()
    assert sub["status"] == "past_due"
    assert sub["school_status"] == "trial"  # still working during the grace period
    assert sub["open_invoices"][0]["overdue"] is True
    clock.at += timedelta(days=3)
    assert (await _run(client, owner))["reminders_sent"] == 1

    clock.at = trial_end + GRACE_PERIOD + timedelta(minutes=1)
    assert (await _run(client, owner))["schools_suspended"] == 1
    assert (await _run(client, owner))["schools_suspended"] == 0
    blocked = await client.get(SUB, headers=bearer(sc["director"]))
    assert blocked.status_code in {401, 403}, blocked.text

    await container.outbox.run_once()
    overdue = [p for n, p in mailbox.events if n == "subscriptions.invoice_overdue"]
    assert len(overdue) >= 2

    # The emailed link works without signing in.
    link = container.use_cases.platform_billing.pay_link(UUID(invoice["id"]))
    token = link.split("token=", 1)[1]
    page = f"/api/v1/platform-invoices/{invoice['id']}"
    bad = await client.get(page, params={"token": "0" * 64})
    assert bad.status_code == 404
    shown = await client.get(page, params={"token": token})
    assert shown.status_code == 200, shown.text
    assert shown.json()["school_name"] == "Latepay Academy"
    assert shown.json()["invoice"]["total_minor"] == BASE + 3 * PER_STUDENT
    assert shown.json()["bank_transfer_details"] == BANK

    started = await client.post(f"{page}/pay", params={"token": token})
    assert started.status_code == 200, started.text
    ref = _ref(paystack, started.json()["invoice"]["authorization_url"])
    paystack.pay(ref)
    confirmed = await client.post(f"{page}/confirm", params={"token": token})
    assert confirmed.json()["invoice"]["status"] == "paid"

    # Paying brings the school straight back.
    back = await client.get(SUB, headers=bearer(sc["director"]))
    assert back.status_code == 200, back.text
    assert back.json()["school_status"] == "active"
    assert back.json()["status"] == "active"
    tenant = (
        await client.get(f"/api/v1/platform/tenants/{sc['id']}", headers=bearer(owner))
    ).json()
    assert tenant["status"] == "active"


async def test_payment_on_an_older_page_is_still_credited(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    paystack: FakePaystack,
    clock: Clock,
) -> None:
    owner = await platform_session(client, container, ["owner"])
    await _plans(client, owner)
    sc = await _paid_school(client, container, mailbox, "twotabs", owner)
    trial_end = datetime.fromisoformat(
        (await client.get(SUB, headers=bearer(sc["director"]))).json()["trial_ends_at"]
    )
    clock.at = trial_end - timedelta(days=5)
    await _run(client, owner)
    invoice = await _open_invoice(client, sc)
    first = await client.post(f"{SUB}/invoices/{invoice['id']}/pay", headers=bearer(sc["director"]))
    old_ref = _ref(paystack, first.json()["authorization_url"])

    clock.at += timedelta(minutes=45)
    second = await client.post(
        f"{SUB}/invoices/{invoice['id']}/pay", headers=bearer(sc["director"])
    )
    new_ref = _ref(paystack, second.json()["authorization_url"])
    assert new_ref != old_ref

    # The school finished paying in the first tab after all.
    paystack.pay(old_ref)
    assert (await _hook(client, paystack, old_ref)).json()["matched"] is True
    history = (await client.get(f"{SUB}/invoices", headers=bearer(sc["director"]))).json()
    assert history["items"][0]["status"] == "paid"


async def test_short_payment_is_not_credited(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    paystack: FakePaystack,
    clock: Clock,
) -> None:
    owner = await platform_session(client, container, ["owner"])
    await _plans(client, owner)
    sc = await _paid_school(client, container, mailbox, "shortpay", owner)
    trial_end = datetime.fromisoformat(
        (await client.get(SUB, headers=bearer(sc["director"]))).json()["trial_ends_at"]
    )
    clock.at = trial_end - timedelta(days=5)
    await _run(client, owner)
    invoice = await _open_invoice(client, sc)
    page = await client.post(f"{SUB}/invoices/{invoice['id']}/pay", headers=bearer(sc["director"]))
    ref = _ref(paystack, page.json()["authorization_url"])
    paystack.pay(ref, amount=100)
    confirmed = await client.post(
        f"{SUB}/invoices/{invoice['id']}/confirm", headers=bearer(sc["director"])
    )
    assert confirmed.json()["status"] == "open"
    trail = (
        await client.get(
            "/api/v1/platform/audit-events",
            headers=bearer(owner),
            params={"action": "subscription.payment_mismatch"},
        )
    ).json()["items"]
    assert len(trail) == 1


async def test_console_records_transfers_waives_voids_and_free_plans_roll(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    paystack: FakePaystack,
    clock: Clock,
) -> None:
    owner = await platform_session(client, container, ["owner"])
    support = await platform_session(client, container, ["support_agent"])
    await _plans(client, owner)
    sc = await _paid_school(client, container, mailbox, "banker", owner)
    free = await _school(client, container, mailbox, "freebie")
    trial_end = datetime.fromisoformat(
        (await client.get(SUB, headers=bearer(sc["director"]))).json()["trial_ends_at"]
    )
    clock.at = trial_end - timedelta(days=2)
    report = await _run(client, owner)
    assert report["invoices_issued"] == 1
    assert report["periods_renewed"] == 0  # free plans roll on the day, not early
    invoice = await _open_invoice(client, sc)

    listed = await client.get(
        f"{CONSOLE}/invoices", headers=bearer(support), params={"status": "open"}
    )
    assert listed.status_code == 200
    assert [i["id"] for i in listed.json()["items"]] == [invoice["id"]]
    denied = await client.post(
        f"{CONSOLE}/invoices/{invoice['id']}/payments",
        headers=bearer(support),
        json={"method": "bank_transfer", "reference": "TRF-1"},
    )
    assert denied.status_code == 403

    # Cancel and re-issue at a new price.
    voided = await client.post(
        f"{CONSOLE}/invoices/{invoice['id']}/void",
        headers=bearer(owner),
        json={"reason": "Agreed a discount"},
    )
    assert voided.json()["status"] == "void"
    assert (await _run(client, owner))["invoices_issued"] == 1
    fresh = await _open_invoice(client, sc)
    assert fresh["id"] != invoice["id"]
    assert fresh["number"] != invoice["number"]

    no_ref = await client.post(
        f"{CONSOLE}/invoices/{fresh['id']}/payments",
        headers=bearer(owner),
        json={"method": "bank_transfer"},
    )
    assert no_ref.status_code == 422
    no_note = await client.post(
        f"{CONSOLE}/invoices/{fresh['id']}/payments",
        headers=bearer(owner),
        json={"method": "waived"},
    )
    assert no_note.status_code == 422
    recorded = await client.post(
        f"{CONSOLE}/invoices/{fresh['id']}/payments",
        headers=bearer(owner),
        json={"method": "bank_transfer", "reference": "TRF-20261127-01"},
    )
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["status"] == "paid"
    assert recorded.json()["settlement_reference"] == "TRF-20261127-01"
    twice = await client.post(
        f"{CONSOLE}/invoices/{fresh['id']}/void", headers=bearer(owner), json={"reason": "oops"}
    )
    assert twice.status_code == 409

    # The free school rolls into its first period at the end of its trial.
    clock.at = trial_end + timedelta(minutes=5)
    assert (await _run(client, owner))["periods_renewed"] == 1
    sub = (await client.get(SUB, headers=bearer(free["director"]))).json()
    assert sub["is_free"] is True
    assert sub["school_status"] == "active"
    assert sub["open_invoices"] == []


async def test_billing_never_lifts_a_suspension_it_did_not_make(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    paystack: FakePaystack,
    clock: Clock,
) -> None:
    owner = await platform_session(client, container, ["owner"])
    await _plans(client, owner)
    sc = await _paid_school(client, container, mailbox, "flagged", owner)
    trial_end = datetime.fromisoformat(
        (await client.get(SUB, headers=bearer(sc["director"]))).json()["trial_ends_at"]
    )
    clock.at = trial_end - timedelta(days=2)
    await _run(client, owner)
    invoice = await _open_invoice(client, sc)
    suspended = await client.post(
        f"/api/v1/platform/tenants/{sc['id']}/status",
        headers=bearer(owner),
        json={"action": "suspend", "reason": "Terms of service review"},
    )
    assert suspended.status_code == 200, suspended.text
    paid = await client.post(
        f"{CONSOLE}/invoices/{invoice['id']}/payments",
        headers=bearer(owner),
        json={"method": "bank_transfer", "reference": "TRF-9"},
    )
    assert paid.json()["status"] == "paid"
    tenant = (
        await client.get(f"/api/v1/platform/tenants/{sc['id']}", headers=bearer(owner))
    ).json()
    assert tenant["status"] == "suspended"
    assert tenant["status_reason"] == "Terms of service review"


async def test_per_student_plan_with_no_students_owes_nothing(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    paystack: FakePaystack,
    clock: Clock,
) -> None:
    from tests.integration.helpers import register_and_verify

    owner = await platform_session(client, container, ["owner"])
    await _plans(client, owner)
    d = await register_and_verify(client, container, mailbox, slug="empty", email="h@empty.ng")
    moved = await client.put(
        f"{CONSOLE}/schools/{d['tenant_id']}/plan",
        headers=bearer(owner),
        json={"plan_code": "perhead"},
    )
    assert moved.status_code == 200, moved.text
    trial_end = datetime.fromisoformat(moved.json()["trial_ends_at"])
    clock.at = trial_end - timedelta(days=1)
    assert (await _run(client, owner))["invoices_issued"] == 1
    [invoice] = (await client.get(f"{SUB}/invoices", headers=bearer(d))).json()["items"]
    assert invoice["total_minor"] == 0
    assert invoice["status"] == "paid"
    assert invoice["settlement_method"] == "waived"


class _Outbox:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


async def test_invoice_emails_carry_the_pay_link_and_bank_details(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    paystack: FakePaystack,
    clock: Clock,
    owner_dsn: str,
) -> None:
    owner = await platform_session(client, container, ["owner"])
    await _plans(client, owner)
    sc = await _paid_school(client, container, mailbox, "mailme", owner)
    # A school that predates billing has no subscription; the cycle adds one.
    conn = await asyncpg.connect(owner_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.rls_bypass', 'on', true)")
            await conn.execute(
                "DELETE FROM platform_subscriptions WHERE tenant_id = $1", UUID(sc["id"])
            )
    finally:
        await conn.close()
    trial_end = datetime.fromisoformat(
        (await client.get(SUB, headers=bearer(sc["director"]))).json()["trial_ends_at"]
    )
    clock.at = trial_end - timedelta(days=6)
    report = await _run(client, owner)
    assert report["subscriptions_created"] == 1
    assert report["invoices_issued"] == 1
    clock.at = trial_end + timedelta(days=1)
    await _run(client, owner)
    await container.outbox.run_once()

    billing = container.use_cases.platform_billing
    email = _Outbox()
    send = SendPlatformInvoiceEmail(
        email=email,
        uow_factory=billing._uow_factory,
        pay_link=billing.pay_link,
        bank_transfer_details=BANK,
    )
    for name, payload in mailbox.events:
        if name.startswith("subscriptions."):
            await send(payload)
    issued, overdue = email.sent
    invoice = await _open_invoice(client, sc)
    assert issued.to == "h@mailme.ng"
    assert invoice["number"] in issued.subject
    assert "NGN 57,500.00" in issued.text
    assert billing.pay_link(UUID(invoice["id"])) in issued.text
    assert BANK in issued.text
    assert overdue.subject.startswith("Overdue:")
    assert "paused" in overdue.text
