"""Safeguarding, end to end over HTTP."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest

from app.infrastructure.container import Container
from app.infrastructure.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.use_cases.safeguarding.alerts import SendUrgentConcernAlert
from app.use_cases.shared.ports import EmailMessage
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer
from tests.integration.test_finance_flows import _school

pytestmark = pytest.mark.integration

SG = "/api/v1/safeguarding"
ACCOUNT = "Ada said her uncle hits her when she comes home late. Bruise on left arm."


class _Outbox:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


async def test_concern_to_closed_case(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    owner_dsn: str,
    app_dsn: str,
) -> None:
    sc = await _school(client, container, mailbox, "safe")
    teacher, director, bursar = sc["teacher"], sc["director"], sc["bursar"]
    seen = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    made = await client.post(
        f"{SG}/concerns",
        headers=bearer(teacher),
        json={
            "student_id": sc["s1"]["user_id"],
            "category": "physical",
            "severity": "high",
            "account": ACCOUNT,
            "observed_at": seen,
            "action_taken": "Reassured her and told the head teacher.",
        },
    )
    assert made.status_code == 201, made.text
    assert made.headers["cache-control"] == "no-store"
    concern_id = made.json()["concern"]["id"]
    assert made.json()["concern"]["student_name"] == "Ada Obi"

    future = await client.post(
        f"{SG}/concerns",
        headers=bearer(teacher),
        json={
            "student_id": sc["s1"]["user_id"],
            "category": "other",
            "severity": "low",
            "account": "x",
            "observed_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert future.status_code == 422
    not_student = await client.post(
        f"{SG}/concerns",
        headers=bearer(teacher),
        json={
            "student_id": bursar["user_id"],
            "category": "other",
            "severity": "low",
            "account": "x",
            "observed_at": seen,
        },
    )
    assert not_student.status_code == 404
    pupil = await client.post(
        f"{SG}/concerns",
        headers=bearer(sc["s2"]),
        json={
            "student_id": sc["s1"]["user_id"],
            "category": "bullying",
            "severity": "low",
            "account": "x",
            "observed_at": seen,
        },
    )
    assert pupil.status_code == 403

    # The reporter sees their report and its status, not the case.
    mine = (await client.get(f"{SG}/my-reports", headers=bearer(teacher))).json()["items"]
    assert [m["account"] for m in mine] == [ACCOUNT]
    for path in (f"/concerns/{concern_id}", "/concerns", "/counts"):
        assert (await client.get(f"{SG}{path}", headers=bearer(teacher))).status_code == 403
    assert (await client.get(f"{SG}/concerns", headers=bearer(bursar))).status_code == 403

    # The lead is told in their inbox, without details.
    notes = (await client.get("/api/v1/notifications", headers=bearer(director))).json()["items"]
    assert [(n["link_type"], n["link_id"]) for n in notes] == [("safeguarding_concern", concern_id)]
    assert "Ada" not in notes[0]["body"]

    listed = (await client.get(f"{SG}/concerns", headers=bearer(director))).json()["items"]
    assert [c["id"] for c in listed] == [concern_id]
    case = (await client.get(f"{SG}/concerns/{concern_id}", headers=bearer(director))).json()
    assert case["account"] == ACCOUNT
    assert [e["kind"] for e in case["chronology"]] == ["note"]
    assert case["chronology"][0]["text"].startswith("Action taken at the time:")

    # Case work: parents contacted, referral, a lead, then closed with the outcome.
    r = await client.post(
        f"{SG}/concerns/{concern_id}/notes",
        headers=bearer(director),
        json={"text": "Spoke to mother by phone.", "parent_contact": True},
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"{SG}/concerns/{concern_id}/referrals",
        headers=bearer(director),
        json={"agency": "Social services", "reference": "SS-2026-114", "text": "Referred today."},
    )
    assert r.json()["concern"]["status"] == "referred"
    wrong_lead = await client.post(
        f"{SG}/concerns/{concern_id}/lead",
        headers=bearer(director),
        json={"lead_id": bursar["user_id"]},
    )
    assert wrong_lead.status_code == 422
    led = await client.post(
        f"{SG}/concerns/{concern_id}/lead",
        headers=bearer(director),
        json={"lead_id": director["user_id"]},
    )
    assert led.json()["concern"]["lead_id"] == director["user_id"]
    closed = await client.post(
        f"{SG}/concerns/{concern_id}/status",
        headers=bearer(director),
        json={"status": "closed", "reason": "Family support plan in place."},
    )
    assert closed.json()["concern"]["closed_at"] is not None
    late = await client.post(
        f"{SG}/concerns/{concern_id}/notes", headers=bearer(director), json={"text": "More"}
    )
    assert late.status_code == 409
    assert late.json()["code"] == "case_closed"
    reopened = await client.post(
        f"{SG}/concerns/{concern_id}/status",
        headers=bearer(director),
        json={"status": "open", "reason": "New disclosure."},
    )
    assert reopened.json()["concern"]["status"] == "open"
    kinds = [e["kind"] for e in reopened.json()["chronology"]]
    assert kinds == ["note", "parent_contact", "referral", "assigned", "status", "status"]
    counts = (await client.get(f"{SG}/counts", headers=bearer(director))).json()
    assert counts["open"] == 1
    assert counts["urgent_open"] == 0
    history = await client.get(
        f"{SG}/concerns", headers=bearer(director), params={"student_id": sc["s1"]["user_id"]}
    )
    assert len(history.json()["items"]) == 1

    # Text is encrypted at rest; the report can't be changed or deleted by the app.
    conn = await asyncpg.connect(owner_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.rls_bypass', 'on', true)")
            stored = await conn.fetchval("SELECT summary_ciphertext FROM safeguarding_concerns")
            notes_stored = await conn.fetch("SELECT text_ciphertext FROM safeguarding_entries")
    finally:
        await conn.close()
    assert "uncle" not in stored
    assert all("mother" not in r["text_ciphertext"] for r in notes_stored)
    conn = await asyncpg.connect(app_dsn)
    try:
        for sql in (
            "UPDATE safeguarding_concerns SET summary_ciphertext = 'x'",
            "UPDATE safeguarding_concerns SET severity = 'low'",
            "DELETE FROM safeguarding_concerns",
            "UPDATE safeguarding_entries SET text_ciphertext = 'x'",
            "DELETE FROM safeguarding_entries",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(sql)
    finally:
        await conn.close()

    # Every step is logged, and the log never holds what was written.
    log = await client.get(
        "/api/v1/audit-events", headers=bearer(director), params={"action": "safeguarding."}
    )
    events = log.json()["items"]
    assert {e["action"] for e in events} >= {
        "safeguarding.concern_reported",
        "safeguarding.case_opened",
        "safeguarding.parent_contact_recorded",
        "safeguarding.referred",
        "safeguarding.lead_assigned",
        "safeguarding.status_changed",
    }
    dumped = json.dumps(events)
    assert "uncle" not in dumped
    assert "mother" not in dumped


async def test_urgent_concern_emails_the_leads(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _school(client, container, mailbox, "safeurgent")
    made = await client.post(
        f"{SG}/concerns",
        headers=bearer(sc["teacher"]),
        json={
            "student_id": sc["s2"]["user_id"],
            "category": "self_harm",
            "severity": "urgent",
            "account": "Bola talked about hurting herself tonight.",
            "observed_at": datetime.now(UTC).isoformat(),
        },
    )
    assert made.status_code == 201, made.text
    await container.outbox.run_once()
    events = [p for n, p in mailbox.events if n == "safeguarding.urgent_concern"]
    assert len(events) == 1
    box = _Outbox()
    await SendUrgentConcernAlert(
        email=box,
        uow_factory=SqlAlchemyUnitOfWorkFactory(container.session_factory),
        school_app_url="https://app.example",
    )(events[0])
    assert [m.to for m in box.sent] == ["h@safeurgent.ng"]
    assert "Bola" not in box.sent[0].text
    assert made.json()["concern"]["id"] in box.sent[0].text
    counts = (await client.get(f"{SG}/counts", headers=bearer(sc["director"]))).json()
    assert counts["urgent_open"] == 1
