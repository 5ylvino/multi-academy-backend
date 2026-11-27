"""Student registers and staff check-in over HTTP."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from masms.infrastructure.container import Container
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer, register_and_verify

pytestmark = pytest.mark.integration

PASSWORD = "Member-Passphrase-2026"
ACA = "/api/v1/academics"
ATT = "/api/v1/attendance"


def _today() -> date:
    return datetime.now(ZoneInfo("Africa/Lagos")).date()


async def _member(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    director: dict[str, Any],
    *,
    slug: str,
    email: str,
    role: str,
    name: str = "Ada Obi",
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
    """A school with a current term around today, one class, its teacher and two students."""
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

    teacher = await _member(
        client, container, mailbox, d, slug=slug, email=f"t@{slug}.ng", role="class_teacher"
    )
    other_teacher = await _member(
        client, container, mailbox, d, slug=slug, email=f"o@{slug}.ng", role="class_teacher"
    )
    s1 = await _member(
        client,
        container,
        mailbox,
        d,
        slug=slug,
        email=f"s1@{slug}.ng",
        role="student",
        name="Bola Ade",
    )
    s2 = await _member(
        client,
        container,
        mailbox,
        d,
        slug=slug,
        email=f"s2@{slug}.ng",
        role="student",
        name="Chika Eze",
    )
    klass = await client.post(
        f"{ACA}/classes",
        headers=h,
        json={
            "name": "Primary 3",
            "code": "P3",
            "school_level": "primary",
            "class_teacher_id": teacher["user_id"],
        },
    )
    assert klass.status_code == 201, klass.text
    for student in (s1, s2):
        r = await client.post(
            f"{ACA}/enrolments",
            headers=h,
            json={"student_id": student["user_id"], "class_id": klass.json()["id"]},
        )
        assert r.status_code == 201, r.text
    return {
        "director": d,
        "teacher": teacher,
        "other_teacher": other_teacher,
        "s1": s1,
        "s2": s2,
        "class_id": klass.json()["id"],
    }


async def test_register_lifecycle(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, owner_dsn: str
) -> None:
    sc = await _school(client, container, mailbox, "reg")
    t, cid = bearer(sc["teacher"]), sc["class_id"]

    empty = await client.get(f"{ATT}/classes/{cid}/register", headers=t)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body["day"] == str(_today())
    assert body["complete"] is False
    assert [e["full_name"] for e in body["entries"]] == ["Bola Ade", "Chika Eze"]
    assert all(e["status"] is None for e in body["entries"])

    first = await client.post(
        f"{ATT}/classes/{cid}/register",
        headers=t,
        json={"entries": [{"student_id": sc["s1"]["user_id"], "status": "present"}]},
    )
    assert first.status_code == 201, first.text
    assert first.json() == {"recorded": 1, "already_marked": []}

    # Resubmitting never overwrites; the already-marked student is reported back.
    second = await client.post(
        f"{ATT}/classes/{cid}/register",
        headers=t,
        json={
            "entries": [
                {"student_id": sc["s1"]["user_id"], "status": "absent"},
                {"student_id": sc["s2"]["user_id"], "status": "absent", "note": "no word"},
            ]
        },
    )
    assert second.json() == {"recorded": 1, "already_marked": [sc["s1"]["user_id"]]}

    full = (await client.get(f"{ATT}/classes/{cid}/register", headers=t)).json()
    assert full["complete"] is True
    assert {e["status"] for e in full["entries"]} == {"present", "absent"}

    fixed = await client.post(
        f"{ATT}/classes/{cid}/corrections",
        headers=bearer(sc["director"]),
        json={
            "day": str(_today()),
            "student_id": sc["s2"]["user_id"],
            "status": "excused",
            "reason": "Parent sent a medical note",
        },
    )
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["corrected"] is True

    summary = await client.get(
        f"{ATT}/students/{sc['s2']['user_id']}/summary", headers=bearer(sc["director"])
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["excused"] == 1
    assert summary.json()["attendance_rate"] == 0.0

    own = await client.get(
        f"{ATT}/students/{sc['s1']['user_id']}/summary", headers=bearer(sc["s1"])
    )
    assert own.status_code == 200
    assert own.json()["attendance_rate"] == 1.0
    peer = await client.get(
        f"{ATT}/students/{sc['s2']['user_id']}/summary", headers=bearer(sc["s1"])
    )
    assert peer.status_code == 403


async def test_register_rules(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _school(client, container, mailbox, "rules")
    cid = sc["class_id"]
    entry = [{"student_id": sc["s1"]["user_id"], "status": "present"}]

    not_mine = await client.post(
        f"{ATT}/classes/{cid}/register",
        headers=bearer(sc["other_teacher"]),
        json={"entries": entry},
    )
    assert not_mine.status_code == 403
    future = await client.post(
        f"{ATT}/classes/{cid}/register",
        headers=bearer(sc["teacher"]),
        json={"day": str(_today() + timedelta(days=1)), "entries": entry},
    )
    assert future.status_code == 422
    stranger = await client.post(
        f"{ATT}/classes/{cid}/register",
        headers=bearer(sc["teacher"]),
        json={"entries": [{"student_id": sc["teacher"]["user_id"], "status": "present"}]},
    )
    assert stranger.status_code == 422
    dup = await client.post(
        f"{ATT}/classes/{cid}/register",
        headers=bearer(sc["teacher"]),
        json={"entries": entry + entry},
    )
    assert dup.status_code == 422
    # Teachers can't correct (no student_attendance:update for class teachers).
    no_rights = await client.post(
        f"{ATT}/classes/{cid}/corrections",
        headers=bearer(sc["teacher"]),
        json={
            "day": str(_today()),
            "student_id": sc["s1"]["user_id"],
            "status": "late",
            "reason": "arrived late",
        },
    )
    assert no_rights.status_code in (403, 404)


async def test_staff_check_in(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _school(client, container, mailbox, "gate")
    admin, teacher = bearer(sc["director"]), bearer(sc["teacher"])

    defaults = await client.get(f"{ATT}/staff/settings", headers=admin)
    assert defaults.status_code == 200, defaults.text
    assert "class_teacher" in defaults.json()["check_in_roles"]
    assert "director" not in defaults.json()["check_in_roles"]

    campus = {"name": "Main campus", "latitude": 6.5244, "longitude": 3.3792, "radius_m": 150}
    saved = await client.put(
        f"{ATT}/staff/settings",
        headers=admin,
        json={
            "window_opens": "00:00:00",
            "late_after": "23:59:58",
            "window_closes": "23:59:59",
            "business_days": [1, 2, 3, 4, 5, 6, 7],
            "check_in_roles": ["class_teacher"],
            "sites": [campus],
            "require_location": True,
        },
    )
    assert saved.status_code == 200, saved.text
    bad_role = await client.put(
        f"{ATT}/staff/settings",
        headers=admin,
        json={**saved.json(), "check_in_roles": ["student"]},
    )
    assert bad_role.status_code == 422

    code = (await client.get(f"{ATT}/staff/kiosk-code", headers=admin)).json()
    assert len(code["code"]) == 6
    assert 0 < code["expires_in"] <= 60
    assert (await client.get(f"{ATT}/staff/kiosk-code", headers=teacher)).status_code == 403

    wrong = await client.post(
        f"{ATT}/staff/check-in",
        headers=teacher,
        json={
            "code": "000000" if code["code"] != "000000" else "111111",
            "latitude": 6.5245,
            "longitude": 3.3793,
        },
    )
    assert wrong.status_code == 422
    away = await client.post(
        f"{ATT}/staff/check-in",
        headers=teacher,
        json={"code": code["code"], "latitude": 6.60, "longitude": 3.30},
    )
    assert away.status_code == 422
    assert away.json()["code"] == "outside_site"

    ok = await client.post(
        f"{ATT}/staff/check-in",
        headers=teacher,
        json={"code": code["code"], "latitude": 6.5245, "longitude": 3.3793},
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["status"] == "present"
    assert ok.json()["site_name"] == "Main campus"
    again = await client.post(
        f"{ATT}/staff/check-in",
        headers=teacher,
        json={"code": code["code"], "latitude": 6.5245, "longitude": 3.3793},
    )
    assert again.status_code == 409

    student = await client.post(
        f"{ATT}/staff/check-in",
        headers=bearer(sc["s1"]),
        json={"code": code["code"], "latitude": 6.5245, "longitude": 3.3793},
    )
    assert student.status_code == 403

    register = (await client.get(f"{ATT}/staff/register", headers=admin)).json()
    by_id = {r["staff_id"]: r for r in register}
    assert by_id[sc["teacher"]["user_id"]]["status"] == "present"
    assert by_id[sc["other_teacher"]["user_id"]]["status"] == "pending"

    mine = (await client.get(f"{ATT}/staff/me", headers=teacher)).json()
    assert mine[0]["day"] == str(_today())
    assert mine[0]["status"] == "present"


async def test_schools_cannot_see_each_others_registers(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    a = await _school(client, container, mailbox, "north")
    b = await register_and_verify(client, container, mailbox, slug="south", email="h@south.ng")
    resp = await client.get(f"{ATT}/classes/{a['class_id']}/register", headers=bearer(b))
    assert resp.status_code in (404, 422)
