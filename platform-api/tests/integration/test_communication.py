"""Announcements, conversations and the notification inbox, end to end over HTTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import pytest

from app.infrastructure.container import Container
from app.infrastructure.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.use_cases.communication.alerts import SendUrgentAnnouncement
from app.use_cases.shared.ports import EmailMessage
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer
from tests.integration.test_finance_flows import _school
from tests.integration.test_payment_proofs_flows import _parent

pytestmark = pytest.mark.integration

ANN = "/api/v1/announcements"
CONV = "/api/v1/conversations"
NOTE = "/api/v1/notifications"


class _Outbox:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


async def _setup(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, slug: str
) -> dict[str, Any]:
    sc = await _school(client, container, mailbox, slug)
    sc["parent"] = await _parent(client, container, mailbox, sc, slug)  # parent of s1 (P3)
    made = await client.patch(
        f"/api/v1/academics/classes/{sc['classes']['P3']}",
        headers=bearer(sc["director"]),
        json={"class_teacher_id": sc["teacher"]["user_id"]},
    )
    assert made.status_code == 200, made.text
    return sc


async def _feed(client: httpx.AsyncClient, who: dict[str, Any]) -> list[str]:
    body = (await client.get(ANN, headers=bearer(who))).json()
    return [a["title"] for a in body["pinned"] + body["items"]]


async def test_announcements_reach_the_right_people(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _setup(client, container, mailbox, "notices")
    director, teacher, parent = sc["director"], sc["teacher"], sc["parent"]
    p3, jss1 = sc["classes"]["P3"], sc["classes"]["JSS1"]

    whole = await client.post(
        ANN,
        headers=bearer(director),
        json={
            "title": "Mid-term break",
            "body": "School closes on Friday at noon.",
            "roles": ["parent", "student"],
            "pinned": True,
        },
    )
    assert whole.status_code == 201, whole.text
    assert whole.json()["state"] == "live"
    assert whole.json()["author_name"]

    # Teachers announce only to classes they teach.
    everyone = {"title": "Bring crayons", "body": "Tomorrow, please.", "roles": ["parent"]}
    wide = await client.post(ANN, headers=bearer(teacher), json=everyone)
    assert wide.status_code == 403
    assert wide.json()["code"] == "outside_your_classes"
    other = await client.post(ANN, headers=bearer(teacher), json={**everyone, "class_ids": [jss1]})
    assert other.status_code == 403
    mine = await client.post(
        ANN,
        headers=bearer(teacher),
        json={**everyone, "roles": ["parent", "student"], "class_ids": [p3]},
    )
    assert mine.status_code == 201, mine.text

    later = await client.post(
        ANN,
        headers=bearer(director),
        json={
            "title": "Sports day",
            "body": "Next month.",
            "roles": ["parent"],
            "publish_at": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
        },
    )
    assert later.json()["state"] == "scheduled"

    assert await _feed(client, parent) == ["Mid-term break", "Bring crayons"]
    assert await _feed(client, sc["s1"]) == ["Mid-term break", "Bring crayons"]
    assert await _feed(client, sc["s3"]) == ["Mid-term break"]  # JSS1, not P3
    assert await _feed(client, sc["bursar"]) == []

    # Reading is recorded; the badge goes down; publishers see the count.
    unread = (await client.get(f"{NOTE}/unread", headers=bearer(parent))).json()
    assert unread == {"notifications": 0, "announcements": 2}
    opened = await client.get(f"{ANN}/{whole.json()['id']}", headers=bearer(parent))
    assert opened.json()["read_at"] is not None
    again = await client.get(f"{ANN}/{whole.json()['id']}", headers=bearer(parent))
    assert again.status_code == 200
    assert (await client.get(f"{NOTE}/unread", headers=bearer(parent))).json()["announcements"] == 1
    managed = (await client.get(f"{ANN}/manage", headers=bearer(director))).json()["items"]
    reads = {a["title"]: a["reads"] for a in managed}
    assert reads == {"Sports day": 0, "Bring crayons": 0, "Mid-term break": 1}
    scheduled = await client.get(
        f"{ANN}/manage", headers=bearer(director), params={"state": "scheduled"}
    )
    assert [a["title"] for a in scheduled.json()["items"]] == ["Sports day"]
    # Not in the audience: not found.
    assert (
        await client.get(f"{ANN}/{whole.json()['id']}", headers=bearer(sc["bursar"]))
    ).status_code == 404

    # Editing and withdrawing are recorded; a withdrawn one leaves the feed.
    edit = await client.put(
        f"{ANN}/{mine.json()['id']}",
        headers=bearer(teacher),
        json={
            **everyone,
            "roles": ["parent", "student"],
            "class_ids": [p3],
            "title": "Bring crayons and paper",
        },
    )
    assert edit.json()["edited_at"] is not None
    assert (
        await client.put(f"{ANN}/{whole.json()['id']}", headers=bearer(teacher), json=everyone)
    ).status_code == 403
    gone = await client.post(
        f"{ANN}/{whole.json()['id']}/withdraw",
        headers=bearer(director),
        json={"reason": "Date moved"},
    )
    assert gone.json()["state"] == "withdrawn"
    assert await _feed(client, parent) == ["Bring crayons and paper"]
    assert (
        await client.get(f"{ANN}/{whole.json()['id']}", headers=bearer(parent))
    ).status_code == 404

    log = await client.get(
        "/api/v1/audit-events", headers=bearer(director), params={"action": "communication."}
    )
    actions = {e["action"] for e in log.json()["items"]}
    assert {
        "communication.announcement_published",
        "communication.announcement_edited",
        "communication.announcement_withdrawn",
    } <= actions


async def test_urgent_alerts_are_emailed_to_the_audience(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _setup(client, container, mailbox, "alerts")
    director, p3 = sc["director"], sc["classes"]["P3"]
    alert = {
        "title": "Flooding on the main road",
        "body": "Pick-up moves to the back gate today.",
        "roles": ["parent", "student", "class_teacher"],
        "class_ids": [p3],
        "priority": "urgent",
    }
    assert (await client.post(ANN, headers=bearer(sc["teacher"]), json=alert)).status_code == 403
    scheduled = await client.post(
        ANN,
        headers=bearer(director),
        json={**alert, "publish_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat()},
    )
    assert scheduled.status_code == 422
    sent = await client.post(ANN, headers=bearer(director), json=alert)
    assert sent.status_code == 201, sent.text

    await container.outbox.run_once()
    events = [p for n, p in mailbox.events if n == "communication.urgent_announcement"]
    assert len(events) == 1
    box = _Outbox()
    await SendUrgentAnnouncement(
        email=box,
        uow_factory=SqlAlchemyUnitOfWorkFactory(container.session_factory),
        school_app_url="https://app.example",
    )(events[0])
    # P3 is s1 and s2, s1's parent, and its class teacher; nobody from JSS1.
    assert {m.to for m in box.sent} == {
        "s1@alerts.ng",
        "s2@alerts.ng",
        "parent@alerts.ng",
        "teacher@alerts.ng",
    }
    assert all(m.subject.startswith("URGENT") for m in box.sent)
    assert all(f"/announcements/{sent.json()['id']}" in m.text for m in box.sent)

    # The audience of a sent alert can't be changed afterwards.
    changed = await client.put(
        f"{ANN}/{sent.json()['id']}",
        headers=bearer(director),
        json={k: v for k, v in {**alert, "roles": ["parent"]}.items() if k != "priority"},
    )
    assert changed.status_code == 422


async def test_parents_and_staff_talk_safely(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, app_dsn: str
) -> None:
    sc = await _setup(client, container, mailbox, "talk")
    teacher, parent, director, bursar = sc["teacher"], sc["parent"], sc["director"], sc["bursar"]

    started = await client.post(
        CONV,
        headers=bearer(parent),
        json={
            "subject": "Ada's reading",
            "participant_ids": [teacher["user_id"]],
            "student_id": sc["s1"]["user_id"],
            "body": "How is Ada doing with her reading?",
        },
    )
    assert started.status_code == 201, started.text
    thread = started.json()
    conv_id = thread["conversation"]["id"]
    assert [m["mine"] for m in thread["messages"]] == [True]
    assert {p["name"] for p in thread["conversation"]["participants"]} == {
        "Ngozi Obi",
        "Tunde Teacher",
    }

    # Safeguarding rules.
    not_mine = await client.post(
        CONV,
        headers=bearer(parent),
        json={
            "subject": "About Chidi",
            "participant_ids": [teacher["user_id"]],
            "student_id": sc["s3"]["user_id"],
            "body": "Hello",
        },
    )
    assert not_mine.status_code == 403
    assert not_mine.json()["code"] == "not_guardian"
    student = await client.post(
        CONV,
        headers=bearer(teacher),
        json={"subject": "Homework", "participant_ids": [sc["s2"]["user_id"]], "body": "Hi"},
    )
    assert student.status_code == 422
    assert student.json()["code"] == "student_participant"
    assert (
        await client.post(
            CONV,
            headers=bearer(sc["s1"]),
            json={"subject": "Help", "participant_ids": [teacher["user_id"]], "body": "Hi"},
        )
    ).status_code == 403

    # The teacher is told, replies twice; the parent gets one notice, not two.
    notes = (await client.get(NOTE, headers=bearer(teacher))).json()["items"]
    assert [(n["kind"], n["link_id"]) for n in notes] == [("message", conv_id)]
    for text in ("She is doing well.", "She reads every morning."):
        r = await client.post(
            f"{CONV}/{conv_id}/messages", headers=bearer(teacher), json={"body": text}
        )
        assert r.status_code == 201, r.text
    parent_notes = (
        await client.get(NOTE, headers=bearer(parent), params={"unread_only": True})
    ).json()
    assert len(parent_notes["items"]) == 1
    listed = (await client.get(CONV, headers=bearer(parent))).json()["items"]
    assert listed[0]["unread"] == 2
    assert listed[0]["last_message"]["body"] == "She reads every morning."

    # Opening the thread clears the unread count and the notice.
    opened = (await client.get(f"{CONV}/{conv_id}", headers=bearer(parent))).json()
    assert opened["messages"][0]["body"] == "She reads every morning."
    assert (await client.get(CONV, headers=bearer(parent))).json()["items"][0]["unread"] == 0
    assert (await client.get(f"{NOTE}/unread", headers=bearer(parent))).json()["notifications"] == 0

    # Outsiders can't read it; a leader can, and that is recorded.
    assert (await client.get(f"{CONV}/{conv_id}", headers=bearer(bursar))).status_code == 404
    assert (
        await client.post(f"{CONV}/{conv_id}/messages", headers=bearer(bursar), json={"body": "x"})
    ).status_code == 404
    reviewed = await client.get(f"{CONV}/{conv_id}", headers=bearer(director))
    assert reviewed.status_code == 200
    assert len(reviewed.json()["messages"]) == 3
    log = await client.get(
        "/api/v1/audit-events",
        headers=bearer(director),
        params={"action": "communication.conversation_reviewed"},
    )
    assert len(log.json()["items"]) == 1

    # Messages can't be changed, even by the app's database role.
    conn = await asyncpg.connect(app_dsn)
    try:
        for sql in (
            "UPDATE communication_messages SET body = 'x'",
            "DELETE FROM communication_messages",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(sql)
    finally:
        await conn.close()

    # Mark everything read.
    marked = await client.post(f"{NOTE}/read", headers=bearer(teacher), json={})
    assert marked.json() == {"marked": 1}
    assert UUID(conv_id)
