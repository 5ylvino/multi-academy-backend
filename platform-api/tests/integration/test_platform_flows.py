"""Owner-console flows and tenant isolation."""

from __future__ import annotations

import asyncpg
import httpx
import pytest

from masms.infrastructure.container import Container
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer, platform_session, register_and_verify

pytestmark = pytest.mark.integration


async def test_platform_lists_and_suspends_school(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    school = await register_and_verify(
        client, container, mailbox, slug="suspendme", email="head@suspendme.ng"
    )
    owner = await platform_session(client, container, ["owner"])

    listing = await client.get(
        "/api/v1/platform/tenants", headers=bearer(owner), params={"q": "suspend"}
    )
    assert listing.status_code == 200, listing.text
    items = listing.json()["items"]
    assert [t["slug"] for t in items] == ["suspendme"]
    tenant_id = items[0]["id"]

    # School works before suspension (also warms the runtime cache).
    assert (await client.get("/api/v1/bootstrap", headers=bearer(school))).status_code == 200

    suspended = await client.post(
        f"/api/v1/platform/tenants/{tenant_id}/status",
        headers=bearer(owner),
        json={"action": "suspend", "reason": "Unpaid invoice"},
    )
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["status"] == "suspended"

    # Enforced on the very next request, even with a still-valid access token.
    blocked = await client.get("/api/v1/bootstrap", headers=bearer(school))
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "tenant_unavailable"

    reinstated = await client.post(
        f"/api/v1/platform/tenants/{tenant_id}/status",
        headers=bearer(owner),
        json={"action": "reinstate"},
    )
    assert reinstated.json()["status"] == "trial"
    assert (await client.get("/api/v1/bootstrap", headers=bearer(school))).status_code == 200


async def test_auditor_cannot_suspend(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    await register_and_verify(client, container, mailbox, slug="audited", email="head@audited.ng")
    auditor = await platform_session(client, container, ["auditor"])
    listing = await client.get("/api/v1/platform/tenants", headers=bearer(auditor))
    tenant_id = listing.json()["items"][0]["id"]
    resp = await client.post(
        f"/api/v1/platform/tenants/{tenant_id}/status",
        headers=bearer(auditor),
        json={"action": "suspend", "reason": "x"},
    )
    assert resp.status_code == 403
    assert resp.json()["details"]["missing"] == ["tenants:write"]


async def test_flags_plan_and_override_reach_bootstrap(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, owner_dsn: str
) -> None:
    school = await register_and_verify(
        client, container, mailbox, slug="flags", email="head@flags.ng"
    )
    owner = await platform_session(client, container, ["owner"])
    h = bearer(owner)

    put = await client.put(
        "/api/v1/platform/flags/fees.installments", headers=h, json={"name": "Installments"}
    )
    assert put.status_code == 200, put.text
    await client.put("/api/v1/platform/flags/cbt.exams", headers=h, json={"name": "CBT"})
    boot = (await client.get("/api/v1/bootstrap", headers=bearer(school))).json()
    assert boot["features"] == {"fees.installments": False, "cbt.exams": False}

    plan = await client.put(
        "/api/v1/platform/plans/starter",
        headers=h,
        json={"name": "Starter", "features": ["fees.installments"], "student_limit": 300},
    )
    assert plan.status_code == 200, plan.text
    boot = (await client.get("/api/v1/bootstrap", headers=bearer(school))).json()
    assert boot["features"]["fees.installments"] is True

    tenant_id = boot["tenant_id"]
    ov = await client.put(
        f"/api/v1/platform/tenants/{tenant_id}/flags/cbt.exams", headers=h, json={"enabled": True}
    )
    assert ov.status_code == 204
    boot = (await client.get("/api/v1/bootstrap", headers=bearer(school))).json()
    assert boot["features"]["cbt.exams"] is True

    kill = await client.put(
        "/api/v1/platform/flags/cbt.exams", headers=h, json={"name": "CBT", "kill_switch": True}
    )
    assert kill.status_code == 200
    boot = (await client.get("/api/v1/bootstrap", headers=bearer(school))).json()
    assert boot["features"]["cbt.exams"] is False

    conn = await asyncpg.connect(owner_dsn)
    try:
        actions = [
            r["action"]
            for r in await conn.fetch("SELECT action FROM audit_events ORDER BY created_at")
        ]
    finally:
        await conn.close()
    assert {"flag.upserted", "plan.upserted", "flag.override_set"} <= set(actions)


async def test_row_level_security_isolates_schools(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, app_dsn: str
) -> None:
    a = await register_and_verify(client, container, mailbox, slug="school-a", email="head@a.ng")
    await register_and_verify(client, container, mailbox, slug="school-b", email="head@b.ng")

    conn = await asyncpg.connect(app_dsn)
    try:
        # No tenant set: the app role sees no school users at all.
        assert await conn.fetchval("SELECT count(*) FROM school_users") == 0
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", a["tenant_id"])
            emails = [r["email"] for r in await conn.fetch("SELECT email FROM school_users")]
            assert emails == ["head@a.ng"]
            # Writing a row for another school is refused by the policy.
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await conn.execute(
                    "UPDATE school_users SET tenant_id = gen_random_uuid() "
                    "WHERE email = 'head@a.ng'"
                )
    finally:
        await conn.close()


async def test_health_ready(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["checks"] == {"postgres": "ok", "redis": "ok"}
