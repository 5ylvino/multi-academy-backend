"""Clinic and gate, end to end over HTTP."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import asyncpg
import httpx
import pytest

from app.infrastructure.container import Container
from app.infrastructure.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.use_cases.operations.notifications import SendUrgentClinicEmail
from app.use_cases.shared.ports import EmailMessage
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer
from tests.integration.test_finance_flows import _member, _school, _today
from tests.integration.test_payment_proofs_flows import _parent

pytestmark = pytest.mark.integration

CL = "/api/v1/clinic"
GT = "/api/v1/gate"


class _Outbox:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


async def _setup(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, slug: str
) -> dict[str, Any]:
    sc = await _school(client, container, mailbox, slug)
    sc["parent"] = await _parent(client, container, mailbox, sc, slug)  # mother of s1
    for key, role, name in (("nurse", "nurse", "Nkechi Nurse"), ("guard", "guard", "Garba Guard")):
        sc[key] = await _member(
            client,
            container,
            mailbox,
            sc["director"],
            slug=slug,
            email=f"{key}@{slug}.ng",
            role=role,
            name=name,
        )
    return sc


async def _notices(client: httpx.AsyncClient, who: dict[str, Any]) -> list[dict[str, Any]]:
    r = await client.get("/api/v1/notifications", headers=bearer(who))
    assert r.status_code == 200, r.text
    items: list[dict[str, Any]] = r.json()["items"]
    return items


async def test_health_record_and_clinic_visit(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    owner_dsn: str,
) -> None:
    sc = await _setup(client, container, mailbox, "clinic")
    s1, s2 = sc["s1"]["user_id"], sc["s2"]["user_id"]
    parent, nurse, teacher = sc["parent"], sc["nurse"], sc["teacher"]

    # The parent fills in the record; the nurse can too, a teacher can't.
    record = {
        "blood_group": "O+",
        "genotype": "AS",
        "allergies": ["Peanuts", "peanuts", "Penicillin"],
        "conditions": ["Asthma"],
        "medications": ["Salbutamol inhaler"],
        "notes": "Uses the inhaler before sport.",
        "staff_alert": "Asthma: inhaler in school bag",
        "doctor": "Dr Ade, Lekki Clinic",
    }
    saved = await client.put(f"{CL}/students/{s1}/record", headers=bearer(parent), json=record)
    assert saved.status_code == 200, saved.text
    assert saved.headers["cache-control"] == "no-store"
    assert saved.json()["allergies"] == ["Peanuts", "Penicillin"]
    bad = await client.put(
        f"{CL}/students/{s1}/record", headers=bearer(parent), json={"blood_group": "Z+"}
    )
    assert bad.status_code == 422
    for who, student in ((teacher, s1), (parent, s2)):
        r = await client.put(f"{CL}/students/{student}/record", headers=bearer(who), json=record)
        assert r.status_code == 403

    # Who sees what.
    assert (await client.get(f"{CL}/students/{s1}/record", headers=bearer(sc["s1"]))).json()[
        "genotype"
    ] == "AS"
    assert (
        await client.get(f"{CL}/students/{s1}/record", headers=bearer(teacher))
    ).status_code == 403
    alert = (await client.get(f"{CL}/students/{s1}/alert", headers=bearer(teacher))).json()
    assert alert == {
        "student_id": s1,
        "student_name": "Ada Obi",
        "allergies": ["Peanuts", "Penicillin"],
        "staff_alert": "Asthma: inhaler in school bag",
    }
    assert (
        await client.get(f"{CL}/students/{s1}/alert", headers=bearer(parent))
    ).status_code == 403
    full = await client.get(f"{CL}/students/{s1}/record", headers=bearer(nurse))
    assert full.json()["notes"] == "Uses the inhaler before sport."
    empty = (await client.get(f"{CL}/students/{s2}/record", headers=bearer(nurse))).json()
    assert empty["allergies"] == []
    assert empty["updated_at"] is None

    # A visit: booked in, treated, closed. The parent is told in the app and by email.
    assert (
        await client.post(
            f"{CL}/visits", headers=bearer(teacher), json={"student_id": s1, "complaint": "x"}
        )
    ).status_code == 403
    opened = await client.post(
        f"{CL}/visits",
        headers=bearer(nurse),
        json={"student_id": s1, "complaint": "Wheezing after PE"},
    )
    assert opened.status_code == 201, opened.text
    visit_id = opened.json()["id"]
    hot = await client.patch(
        f"{CL}/visits/{visit_id}", headers=bearer(nurse), json={"temperature_dc": 500}
    )
    assert hot.status_code == 422
    noted = await client.patch(
        f"{CL}/visits/{visit_id}",
        headers=bearer(nurse),
        json={
            "assessment": "Mild asthma attack",
            "treatment": "Two puffs of salbutamol",
            "temperature_dc": 372,
            "medication_given": ["Salbutamol"],
        },
    )
    assert noted.status_code == 200, noted.text
    assert noted.json()["temperature_dc"] == 372
    on_bed = await client.get(f"{CL}/visits", headers=bearer(nurse), params={"open_only": True})
    assert [v["id"] for v in on_bed.json()["items"]] == [visit_id]
    closed = await client.post(
        f"{CL}/visits/{visit_id}/close", headers=bearer(nurse), json={"outcome": "sent_home"}
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["left_at"] is not None
    again = await client.patch(
        f"{CL}/visits/{visit_id}", headers=bearer(nurse), json={"treatment": "More"}
    )
    assert again.status_code == 409
    assert again.json()["code"] == "visit_closed"

    notes = await _notices(client, parent)
    assert [(n["link_type"], n["link_id"]) for n in notes] == [("clinic_visit", visit_id)]
    assert notes[0]["body"] == "Ada was sent home."
    mine = await client.get(f"{CL}/visits", headers=bearer(parent), params={"student_id": s1})
    assert mine.json()["items"][0]["assessment"] == "Mild asthma attack"
    for params in ({}, {"student_id": s2}):
        r = await client.get(f"{CL}/visits", headers=bearer(parent), params=params)
        assert r.status_code == 403

    await container.outbox.run_once()
    events = [p for n, p in mailbox.events if n == "operations.clinic_visit_closed"]
    assert len(events) == 1
    assert events[0]["urgent"] is True
    box = _Outbox()
    await SendUrgentClinicEmail(
        email=box,
        uow_factory=SqlAlchemyUnitOfWorkFactory(container.session_factory),
        school_app_url="https://app.example",
    )(events[0])
    assert [m.to for m in box.sent] == ["parent@clinic.ng"]
    assert box.sent[0].subject == "Ada is being sent home from school"
    assert "asthma" not in box.sent[0].text.lower()

    # Medical detail is encrypted at rest and never reaches the activity log.
    conn = await asyncpg.connect(owner_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.rls_bypass', 'on', true)")
            rec = await conn.fetchrow("SELECT * FROM clinic_health_records")
            vis = await conn.fetchrow("SELECT notes_ciphertext FROM clinic_visits")
    finally:
        await conn.close()
    assert rec is not None
    assert vis is not None
    assert "Peanuts" not in rec["details_ciphertext"]
    assert "Asthma" not in (rec["alert_ciphertext"] or "")
    assert "salbutamol" not in vis["notes_ciphertext"].lower()
    log = await client.get(
        "/api/v1/audit-events", headers=bearer(sc["director"]), params={"action": "clinic."}
    )
    events_log = log.json()["items"]
    assert {e["action"] for e in events_log} >= {
        "clinic.record_updated",
        "clinic.record_viewed",
        "clinic.visit_opened",
        "clinic.visit_updated",
        "clinic.visit_closed",
    }
    dumped = json.dumps(events_log).lower()
    assert "peanut" not in dumped
    assert "wheezing" not in dumped


async def test_rested_in_clinic_sends_no_email(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _setup(client, container, mailbox, "clinicmild")
    opened = await client.post(
        f"{CL}/visits",
        headers=bearer(sc["nurse"]),
        json={"student_id": sc["s1"]["user_id"], "complaint": "Headache"},
    )
    visit_id = opened.json()["id"]
    await client.post(
        f"{CL}/visits/{visit_id}/close",
        headers=bearer(sc["nurse"]),
        json={"outcome": "returned_to_class"},
    )
    await container.outbox.run_once()
    events = [p for n, p in mailbox.events if n == "operations.clinic_visit_closed"]
    assert events[0]["urgent"] is False
    box = _Outbox()
    await SendUrgentClinicEmail(
        email=box,
        uow_factory=SqlAlchemyUnitOfWorkFactory(container.session_factory),
        school_app_url="https://app.example",
    )(events[0])
    assert box.sent == []
    notes = await _notices(client, sc["parent"])
    assert notes[0]["body"] == "Ada returned to class."


async def test_pickup_list_and_release_at_the_gate(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, app_dsn: str
) -> None:
    sc = await _setup(client, container, mailbox, "gate")
    s1, s2 = sc["s1"]["user_id"], sc["s2"]["user_id"]
    parent, guard, teacher = sc["parent"], sc["guard"], sc["teacher"]

    # The parent adds an aunt for every day and a driver for today only.
    aunt = await client.post(
        f"{GT}/students/{s1}/collectors",
        headers=bearer(parent),
        json={"name": "Aunty Ifeoma Obi", "relationship": "aunt", "phone": "+2348030000001"},
    )
    assert aunt.status_code == 201, aunt.text
    driver = await client.post(
        f"{GT}/students/{s1}/collectors",
        headers=bearer(parent),
        json={
            "name": "Musa Driver",
            "relationship": "driver",
            "id_document": "Driver's licence ending 4471",
            "valid_on": str(_today()),
        },
    )
    assert driver.status_code == 201, driver.text
    past = await client.post(
        f"{GT}/students/{s1}/collectors",
        headers=bearer(parent),
        json={
            "name": "Late Entry",
            "relationship": "uncle",
            "valid_on": str(_today() - timedelta(days=1)),
        },
    )
    assert past.status_code == 422
    for who, student in ((teacher, s1), (guard, s1), (parent, s2)):
        r = await client.post(
            f"{GT}/students/{student}/collectors",
            headers=bearer(who),
            json={"name": "Someone Else", "relationship": "uncle"},
        )
        assert r.status_code == 403
    aunt_id, driver_id = aunt.json()["id"], driver.json()["id"]

    # The guard sees who may collect today.
    pickup = await client.get(f"{GT}/students/{s1}/pickup", headers=bearer(guard))
    assert pickup.status_code == 200, pickup.text
    body = pickup.json()
    assert body["student_name"] == "Ada Obi"
    assert [(g["name"], g["relationship"]) for g in body["guardians"]] == [("Ngozi Obi", "mother")]
    assert {c["id"] for c in body["collectors"]} == {aunt_id, driver_id}
    assert (
        await client.get(f"{GT}/students/{s1}/pickup", headers=bearer(parent))
    ).status_code == 403

    # Released to the aunt: the parent is told.
    out = await client.post(
        f"{GT}/releases", headers=bearer(guard), json={"student_id": s1, "collector_id": aunt_id}
    )
    assert out.status_code == 201, out.text
    assert out.json()["status"] == "released"
    assert out.json()["collector_name"] == "Aunty Ifeoma Obi"
    to_mother = await client.post(
        f"{GT}/releases",
        headers=bearer(guard),
        json={"student_id": s1, "guardian_id": parent["user_id"]},
    )
    assert to_mother.json()["collector_name"] == "Ngozi Obi"

    # The aunt can't take a child she isn't listed for; the guard must pick exactly one.
    wrong = await client.post(
        f"{GT}/releases", headers=bearer(guard), json={"student_id": s2, "collector_id": aunt_id}
    )
    assert wrong.status_code == 422
    assert wrong.json()["code"] == "not_authorised"
    not_guardian = await client.post(
        f"{GT}/releases",
        headers=bearer(guard),
        json={"student_id": s2, "guardian_id": parent["user_id"]},
    )
    assert not_guardian.status_code == 422
    both = await client.post(
        f"{GT}/releases",
        headers=bearer(guard),
        json={"student_id": s1, "collector_id": aunt_id, "refused_name": "Stranger Danger"},
    )
    assert both.status_code == 422

    # Removed at once; a stranger is refused and recorded.
    removed = await client.delete(f"{GT}/collectors/{aunt_id}", headers=bearer(parent))
    assert removed.json()["active"] is False
    gone = await client.post(
        f"{GT}/releases", headers=bearer(guard), json={"student_id": s1, "collector_id": aunt_id}
    )
    assert gone.status_code == 422
    refused = await client.post(
        f"{GT}/releases",
        headers=bearer(guard),
        json={
            "student_id": s1,
            "refused_name": "Emeka Stranger",
            "note": "Said he was a cousin, no ID.",
        },
    )
    assert refused.status_code == 201, refused.text
    assert refused.json()["status"] == "refused"
    no_note = await client.post(
        f"{GT}/releases",
        headers=bearer(guard),
        json={"student_id": s1, "refused_name": "Emeka Stranger"},
    )
    assert no_note.status_code == 422

    titles = [n["title"] for n in await _notices(client, parent)]
    assert titles == [
        "Someone tried to collect Ada",
        "Ada has left school",
        "Ada has left school",
    ]
    listed = await client.get(f"{GT}/students/{s1}/collectors", headers=bearer(parent))
    assert {(c["id"], c["active"]) for c in listed.json()} == {(aunt_id, False), (driver_id, True)}

    # The history: guardians see their child's; leaders see all; teachers don't.
    mine = await client.get(f"{GT}/releases", headers=bearer(parent), params={"student_id": s1})
    assert [r["status"] for r in mine.json()["items"]] == ["refused", "released", "released"]
    for params in ({}, {"student_id": s2}):
        r = await client.get(f"{GT}/releases", headers=bearer(parent), params=params)
        assert r.status_code == 403
    assert (await client.get(f"{GT}/releases", headers=bearer(teacher))).status_code == 403
    every = await client.get(f"{GT}/releases", headers=bearer(sc["director"]))
    assert len(every.json()["items"]) == 3

    # Release records can't be changed or removed by the app.
    conn = await asyncpg.connect(app_dsn)
    try:
        for sql in ("UPDATE gate_releases SET status = 'released'", "DELETE FROM gate_releases"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(sql)
    finally:
        await conn.close()

    log = await client.get(
        "/api/v1/audit-events", headers=bearer(sc["director"]), params={"action": "gate."}
    )
    assert {e["action"] for e in log.json()["items"]} >= {
        "gate.collector_added",
        "gate.collector_removed",
        "gate.student_released",
        "gate.student_refused",
    }


async def test_visitor_book(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _setup(client, container, mailbox, "visitors")
    guard, teacher = sc["guard"], sc["teacher"]
    student_host = await client.post(
        f"{GT}/visits",
        headers=bearer(guard),
        json={"name": "Mr Bello", "purpose": "Meeting", "host_id": sc["s1"]["user_id"]},
    )
    assert student_host.status_code == 422
    assert (
        await client.post(
            f"{GT}/visits", headers=bearer(teacher), json={"name": "Mr Bello", "purpose": "x"}
        )
    ).status_code == 403
    signed = await client.post(
        f"{GT}/visits",
        headers=bearer(guard),
        json={
            "name": "Mr Bello",
            "purpose": "Parent-teacher meeting",
            "organisation": "Bello Ltd",
            "phone": "08030000002",
            "host_id": teacher["user_id"],
            "badge": "V-12",
            "vehicle": "LND 123 AB",
        },
    )
    assert signed.status_code == 201, signed.text
    assert signed.json()["host_name"] == "Tunde Teacher"
    visit_id = signed.json()["id"]
    register = await client.get(f"{GT}/visits", headers=bearer(guard), params={"on_site": True})
    assert [v["id"] for v in register.json()["items"]] == [visit_id]
    out = await client.post(f"{GT}/visits/{visit_id}/sign-out", headers=bearer(guard))
    assert out.json()["signed_out_at"] is not None
    twice = await client.post(f"{GT}/visits/{visit_id}/sign-out", headers=bearer(guard))
    assert twice.status_code == 409
    register = await client.get(f"{GT}/visits", headers=bearer(guard), params={"on_site": True})
    assert register.json()["items"] == []
    history = await client.get(f"{GT}/visits", headers=bearer(sc["director"]))
    assert [v["id"] for v in history.json()["items"]] == [visit_id]
    assert (await client.get(f"{GT}/visits", headers=bearer(teacher))).status_code == 403
