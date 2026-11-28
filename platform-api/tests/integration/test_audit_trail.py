"""The audit trail: complete, tamper-resistant, isolated per school, searchable and exportable."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx
import pytest

from app.infrastructure.container import Container
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer, platform_session, register_and_verify, section_id

pytestmark = pytest.mark.integration

STAFF_PASSWORD = "Teacher-Passphrase-2026"
UA = "Mozilla/5.0 (Macintosh) TestBrowser/1.0"


async def _events(
    client: httpx.AsyncClient, session: dict[str, Any], **params: Any
) -> dict[str, Any]:
    resp = await client.get("/api/v1/audit-events", headers=bearer(session), params=params)
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def _school(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, slug: str
) -> dict[str, Any]:
    return await register_and_verify(client, container, mailbox, slug=slug, email=f"head@{slug}.ng")


async def test_changes_are_recorded_with_who_what_and_where(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    director = await _school(client, container, mailbox, "ledgerly")
    created = await client.post(
        "/api/v1/academics/sessions",
        headers={**bearer(director), "X-Request-Id": "req-audit-0001", "User-Agent": UA},
        json={"name": "2026/2027", "starts_on": "2026-09-01", "ends_on": "2027-07-31"},
    )
    assert created.status_code == 201, created.text

    body = await _events(client, director)
    actions = [e["action"] for e in body["items"]]
    # Newest first: the session, then sign-in and verification from signup, then signup.
    assert actions[0] == "academics.session_created"
    assert {"tenant.registered", "tenant.email_verified", "identity.signed_in"} <= set(actions)

    event = body["items"][0]
    assert event["label"] == "Created an academic session"
    assert event["category"] == "academics"
    assert event["actor_name"] == "Ngozi Eze"
    assert event["actor_email"] == "head@ledgerly.ng"
    assert event["actor_type"] == "school"
    assert event["target_type"] == "academic_session"
    assert event["target_id"] == created.json()["id"]
    assert event["details"]["name"] == "2026/2027"
    assert event["request_id"] == "req-audit-0001"
    assert event["ip_address"] == "203.0.113.7"
    assert event["device"] == UA


async def test_updates_record_each_changed_field(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    director = await _school(client, container, mailbox, "fieldwise")
    made = await client.post(
        "/api/v1/academics/classes",
        headers=bearer(director),
        json={"name": "JSS 1A", "code": "JSS1A", "school_level": "secondary", "capacity": 30},
    )
    assert made.status_code == 201, made.text
    changed = await client.patch(
        f"/api/v1/academics/classes/{made.json()['id']}",
        headers=bearer(director),
        json={"name": "JSS 1 Gold", "capacity": 30},
    )
    assert changed.status_code == 200, changed.text

    body = await _events(client, director, action="academics.class_updated")
    assert len(body["items"]) == 1
    assert body["items"][0]["details"]["changes"] == {
        "name": {"from": "JSS 1A", "to": "JSS 1 Gold"}
    }


async def test_failed_sign_in_is_recorded_without_the_password(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    director = await _school(client, container, mailbox, "watchful")
    bad = await client.post(
        "/api/v1/auth/sign-in",
        json={
            "school": "watchful",
            "email": "head@watchful.ng",
            "password": "Wrong-Passphrase-2026",
        },
    )
    assert bad.status_code == 401
    body = await _events(client, director, action="identity.sign_in_failed")
    assert len(body["items"]) == 1
    event = body["items"][0]
    assert event["details"] == {"reason": "wrong_password", "locked": False}
    assert "Wrong-Passphrase" not in str(event)


async def test_schools_only_see_their_own_trail(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    alpha = await _school(client, container, mailbox, "alphatrail")
    beta = await _school(client, container, mailbox, "betatrail")
    alpha_ids = {e["school_id"] for e in (await _events(client, alpha))["items"]}
    beta_ids = {e["school_id"] for e in (await _events(client, beta))["items"]}
    assert len(alpha_ids) == 1
    assert len(beta_ids) == 1
    assert alpha_ids != beta_ids


async def test_pages_follow_on_without_gaps_or_repeats(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    director = await _school(client, container, mailbox, "pager")
    for code, name in (("JSS1A", "JSS 1A"), ("JSS1B", "JSS 1B"), ("JSS1C", "JSS 1C")):
        made = await client.post(
            "/api/v1/academics/classes",
            headers=bearer(director),
            json={"name": name, "code": code, "school_level": "secondary"},
        )
        assert made.status_code == 201, made.text
    everything = [e["id"] for e in (await _events(client, director, limit=200))["items"]]

    seen: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = await _events(client, director, **params)
        seen.extend(e["id"] for e in page["items"])
        cursor = page["page"]["next_cursor"]
        if cursor is None:
            break
    assert seen == everything

    bad = await client.get(
        "/api/v1/audit-events", headers=bearer(director), params={"cursor": "garbage"}
    )
    assert bad.status_code == 422


async def test_only_leaders_can_read_the_trail(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    director = await _school(client, container, mailbox, "guarded")
    invited = await client.post(
        "/api/v1/users",
        headers=bearer(director),
        json={
            "email": "t@guarded.ng",
            "full_name": "Ada Obi",
            "roles": ["subject_teacher"],
            "section_ids": [await section_id(client, director, "primary")],
        },
    )
    assert invited.status_code == 201, invited.text
    await container.outbox.run_once()
    teacher = await client.post(
        "/api/v1/schools/guarded/invites/accept",
        json={"token": mailbox.last_invite_token("t@guarded.ng"), "password": STAFF_PASSWORD},
    )
    assert teacher.status_code == 200, teacher.text
    denied = await client.get("/api/v1/audit-events", headers=bearer(teacher.json()))
    assert denied.status_code == 403
    actions = [e["action"] for e in (await _events(client, director))["items"]]
    assert {"school_user.invited", "identity.invite_accepted"} <= set(actions)


async def test_export_is_csv_and_is_itself_recorded(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    director = await _school(client, container, mailbox, "exporter")
    now = datetime.now(UTC)
    missing_dates = await client.get("/api/v1/audit-events/export", headers=bearer(director))
    assert missing_dates.status_code == 422

    resp = await client.get(
        "/api/v1/audit-events/export",
        headers=bearer(director),
        params={
            "since": (now - timedelta(days=1)).isoformat(),
            "until": (now + timedelta(minutes=5)).isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(resp.text.lstrip("﻿"))))
    assert {r["action"] for r in rows} >= {"tenant.registered", "identity.signed_in"}
    assert all(r["label"] for r in rows)

    latest = (await _events(client, director, limit=1))["items"][0]
    assert latest["action"] == "audit.exported"
    assert latest["details"]["rows"] == len(rows)


async def test_trail_cannot_be_edited_or_deleted(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    app_dsn: str,
    owner_dsn: str,
) -> None:
    await _school(client, container, mailbox, "immutable")
    app_conn = await asyncpg.connect(app_dsn)
    try:
        await app_conn.execute("SET app.rls_bypass = 'on'")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app_conn.execute("UPDATE audit_events SET action = 'x'")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app_conn.execute("DELETE FROM audit_events")
    finally:
        await app_conn.close()

    # Even the table owner is refused by the trigger.
    owner = await asyncpg.connect(owner_dsn)
    try:
        await owner.execute("SET app.rls_bypass = 'on'")
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
            await owner.execute("UPDATE audit_events SET action = 'x'")
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="append-only"):
            await owner.execute("DELETE FROM audit_events")
        # The retention purge only removes rows past the 7-year floor: none here.
        async with owner.transaction():
            await owner.execute("SET LOCAL app.audit_purge = 'on'")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await owner.execute("DELETE FROM audit_events")
        assert await owner.fetchval("SELECT count(*) FROM audit_events") > 0
    finally:
        await owner.close()


async def test_owner_console_sees_every_school_and_its_own_activity(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    await _school(client, container, mailbox, "consoleview")
    auditor = await platform_session(client, container, ["auditor"])
    everything = await client.get("/api/v1/platform/audit-events", headers=bearer(auditor))
    assert everything.status_code == 200, everything.text
    items = everything.json()["items"]
    school_ids = {e["school_id"] for e in items if e["school_id"]}
    assert len(school_ids) == 1

    school_only = await client.get(
        "/api/v1/platform/audit-events",
        headers=bearer(auditor),
        params={"school_id": school_ids.pop()},
    )
    assert all(e["school_id"] for e in school_only.json()["items"])

    console = await client.get(
        "/api/v1/platform/audit-events", headers=bearer(auditor), params={"console_only": True}
    )
    console_actions = {e["action"] for e in console.json()["items"]}
    assert "identity.mfa_enrolled" in console_actions
    assert all(e["school_id"] is None for e in console.json()["items"])

    actions = await client.get("/api/v1/platform/audit-events/actions", headers=bearer(auditor))
    assert actions.status_code == 200
    assert {"action": "finance.payment_recorded", "label": "Recorded a payment"}.items() <= next(
        a for a in actions.json() if a["action"] == "finance.payment_recorded"
    ).items()

    # School tokens can't use console routes.
    director = await register_and_verify(
        client, container, mailbox, slug="nosy", email="head@nosy.ng"
    )
    assert (
        await client.get("/api/v1/platform/audit-events", headers=bearer(director))
    ).status_code in {401, 403}
