"""Academic calendar, classes, subjects and enrolment over HTTP."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.infrastructure.container import Container
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer, register_and_verify

pytestmark = pytest.mark.integration

PASSWORD = "Member-Passphrase-2026"
API = "/api/v1/academics"


async def _member(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    director: dict[str, Any],
    *,
    slug: str,
    email: str,
    role: str,
) -> dict[str, Any]:
    r = await client.post(
        "/api/v1/users",
        headers=bearer(director),
        json={"email": email, "full_name": "Ada Obi", "roles": [role]},
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


async def _calendar(client: httpx.AsyncClient, h: dict[str, str]) -> dict[str, Any]:
    s = await client.post(
        f"{API}/sessions",
        headers=h,
        json={"name": "2026/2027", "starts_on": "2026-09-01", "ends_on": "2027-07-31"},
    )
    assert s.status_code == 201, s.text
    session: dict[str, Any] = s.json()
    t = await client.post(
        f"{API}/sessions/{session['id']}/terms",
        headers=h,
        json={"code": "first", "starts_on": "2026-09-08", "ends_on": "2026-12-18"},
    )
    assert t.status_code == 201, t.text
    term = t.json()
    cur = await client.post(f"{API}/terms/{term['id']}/current", headers=h)
    assert cur.status_code == 200, cur.text
    assert cur.json()["is_current"] is True
    return session


async def test_calendar_rules(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    d = await register_and_verify(client, container, mailbox, slug="cal", email="h@cal.ng")
    h = bearer(d)
    session = await _calendar(client, h)

    overlap = await client.post(
        f"{API}/sessions",
        headers=h,
        json={"name": "2027/2028", "starts_on": "2027-07-01", "ends_on": "2028-07-31"},
    )
    assert overlap.status_code == 409

    backwards = await client.post(
        f"{API}/sessions",
        headers=h,
        json={"name": "2030/2031", "starts_on": "2031-01-01", "ends_on": "2030-01-01"},
    )
    assert backwards.status_code == 422

    dup_term = await client.post(
        f"{API}/sessions/{session['id']}/terms",
        headers=h,
        json={"code": "first", "starts_on": "2027-01-04", "ends_on": "2027-03-31"},
    )
    assert dup_term.status_code == 409

    outside = await client.post(
        f"{API}/sessions/{session['id']}/terms",
        headers=h,
        json={"code": "second", "starts_on": "2027-01-04", "ends_on": "2027-09-30"},
    )
    assert outside.status_code == 422

    second = await client.post(
        f"{API}/sessions/{session['id']}/terms",
        headers=h,
        json={"code": "second", "starts_on": "2027-01-04", "ends_on": "2027-03-31"},
    )
    assert second.status_code == 201
    switched = await client.post(f"{API}/terms/{second.json()['id']}/current", headers=h)
    assert switched.status_code == 200

    listing = (await client.get(f"{API}/sessions", headers=h)).json()
    assert len(listing) == 1
    terms = {t["code"]: t["is_current"] for t in listing[0]["terms"]}
    assert terms == {"first": False, "second": True}
    assert listing[0]["is_current"] is True


async def test_classes_subjects_and_enrolment(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    d = await register_and_verify(client, container, mailbox, slug="rose", email="h@rose.ng")
    h = bearer(d)

    # Enrolling before a current term exists is refused.
    teacher = await _member(
        client, container, mailbox, d, slug="rose", email="t@rose.ng", role="class_teacher"
    )
    s1 = await _member(
        client, container, mailbox, d, slug="rose", email="s1@rose.ng", role="student"
    )
    s2 = await _member(
        client, container, mailbox, d, slug="rose", email="s2@rose.ng", role="student"
    )

    nursery = await client.post(
        f"{API}/classes",
        headers=h,
        json={"name": "Nursery 1", "code": "nur1", "school_level": "nursery"},
    )
    assert nursery.status_code == 422  # the school doesn't offer nursery

    jss1 = await client.post(
        f"{API}/classes",
        headers=h,
        json={
            "name": "JSS 1A",
            "code": "jss 1a",
            "school_level": "secondary",
            "capacity": 1,
            "class_teacher_id": teacher["user_id"],
        },
    )
    assert jss1.status_code == 201, jss1.text
    klass = jss1.json()
    assert klass["code"] == "JSS-1A"

    dup = await client.post(
        f"{API}/classes",
        headers=h,
        json={"name": "Dup", "code": "JSS-1A", "school_level": "secondary"},
    )
    assert dup.status_code == 409

    not_teacher = await client.post(
        f"{API}/classes",
        headers=h,
        json={
            "name": "JSS 1B",
            "code": "JSS-1B",
            "school_level": "secondary",
            "class_teacher_id": s1["user_id"],
        },
    )
    assert not_teacher.status_code == 422

    maths = await client.post(
        f"{API}/subjects",
        headers=h,
        json={"name": "Mathematics", "code": "mth", "school_level": "secondary"},
    )
    assert maths.status_code == 201, maths.text
    phonics = await client.post(
        f"{API}/subjects",
        headers=h,
        json={"name": "Phonics", "code": "phn", "school_level": "primary"},
    )
    assert phonics.status_code == 201

    linked = await client.put(
        f"{API}/classes/{klass['id']}/subjects/{maths.json()['id']}", headers=h, json={}
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["subject_ids"] == [maths.json()["id"]]
    wrong_level = await client.put(
        f"{API}/classes/{klass['id']}/subjects/{phonics.json()['id']}", headers=h, json={}
    )
    assert wrong_level.status_code == 422

    no_term = await client.post(
        f"{API}/enrolments", headers=h, json={"student_id": s1["user_id"], "class_id": klass["id"]}
    )
    assert no_term.status_code == 422

    await _calendar(client, h)
    first = await client.post(
        f"{API}/enrolments", headers=h, json={"student_id": s1["user_id"], "class_id": klass["id"]}
    )
    assert first.status_code == 201, first.text
    again = await client.post(
        f"{API}/enrolments", headers=h, json={"student_id": s1["user_id"], "class_id": klass["id"]}
    )
    assert again.status_code == 201
    assert again.json()["id"] == first.json()["id"]

    full = await client.post(
        f"{API}/enrolments", headers=h, json={"student_id": s2["user_id"], "class_id": klass["id"]}
    )
    assert full.status_code == 409

    bigger = await client.patch(f"{API}/classes/{klass['id']}", headers=h, json={"capacity": 40})
    assert bigger.status_code == 200
    assert (
        await client.post(
            f"{API}/enrolments",
            headers=h,
            json={"student_id": s2["user_id"], "class_id": klass["id"]},
        )
    ).status_code == 201

    roster = (await client.get(f"{API}/classes/{klass['id']}/roster", headers=h)).json()
    assert {r["student_id"] for r in roster} == {s1["user_id"], s2["user_id"]}

    cleared = await client.patch(
        f"{API}/classes/{klass['id']}", headers=h, json={"class_teacher_id": None}
    )
    assert cleared.json()["class_teacher_id"] is None

    # A student can read nothing administrative.
    denied = await client.post(
        f"{API}/subjects",
        headers=bearer(s1),
        json={"name": "Art", "code": "art", "school_level": "secondary"},
    )
    assert denied.status_code == 403

    unlinked = await client.delete(
        f"{API}/classes/{klass['id']}/subjects/{maths.json()['id']}", headers=h
    )
    assert unlinked.json()["subject_ids"] == []


async def test_moving_class_withdraws_previous_enrolment(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    d = await register_and_verify(client, container, mailbox, slug="move", email="h@move.ng")
    h = bearer(d)
    await _calendar(client, h)
    student = await _member(
        client, container, mailbox, d, slug="move", email="s@move.ng", role="student"
    )
    a = (
        await client.post(
            f"{API}/classes",
            headers=h,
            json={"name": "Primary 1", "code": "P1", "school_level": "primary"},
        )
    ).json()
    b = (
        await client.post(
            f"{API}/classes",
            headers=h,
            json={"name": "Primary 2", "code": "P2", "school_level": "primary"},
        )
    ).json()
    for target in (a, b):
        r = await client.post(
            f"{API}/enrolments",
            headers=h,
            json={"student_id": student["user_id"], "class_id": target["id"]},
        )
        assert r.status_code == 201, r.text
    assert (await client.get(f"{API}/classes/{a['id']}/roster", headers=h)).json() == []
    assert len((await client.get(f"{API}/classes/{b['id']}/roster", headers=h)).json()) == 1


async def test_schools_cannot_see_each_others_classes(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    a = await register_and_verify(client, container, mailbox, slug="east", email="h@east.ng")
    b = await register_and_verify(client, container, mailbox, slug="west", email="h@west.ng")
    made = await client.post(
        f"{API}/classes",
        headers=bearer(a),
        json={"name": "JSS 2", "code": "JSS2", "school_level": "secondary"},
    )
    assert made.status_code == 201
    assert (await client.get(f"{API}/classes", headers=bearer(b))).json() == []
    patched = await client.patch(
        f"{API}/classes/{made.json()['id']}", headers=bearer(b), json={"name": "Stolen"}
    )
    assert patched.status_code == 404
    # Same code is free in another school.
    same_code = await client.post(
        f"{API}/classes",
        headers=bearer(b),
        json={"name": "JSS 2", "code": "JSS2", "school_level": "secondary"},
    )
    assert same_code.status_code == 201
