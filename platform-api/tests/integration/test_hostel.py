"""Boarding and exeats, end to end over HTTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx
import pytest

from app.infrastructure.container import Container
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer
from tests.integration.test_clinic_and_gate import _notices, _setup
from tests.integration.test_finance_flows import _member

pytestmark = pytest.mark.integration

HO = "/api/v1/hostels"


def _when(**delta: float) -> str:
    return (datetime.now(UTC) + timedelta(**delta)).isoformat()


async def test_hostel_rooms_and_exeats(
    client: httpx.AsyncClient,
    container: Container,
    mailbox: Mailbox,
    owner_dsn: str,
    app_dsn: str,
) -> None:
    sc = await _setup(client, container, mailbox, "board")
    d, teacher, parent = sc["director"], sc["teacher"], sc["parent"]
    s1, s2, s3 = (sc[k]["user_id"] for k in ("s1", "s2", "s3"))
    warden = await _member(
        client,
        container,
        mailbox,
        d,
        slug="board",
        email="warden@board.ng",
        role="principal",
        name="Wale Warden",
    )

    # Setup: a hostel with a warden and two rooms.
    made = await client.post(
        HO,
        headers=bearer(d),
        json={"name": "Queen Amina House", "kind": "girls", "warden_id": warden["user_id"]},
    )
    assert made.status_code == 201, made.text
    hid = made.json()["id"]
    assert made.json()["warden_name"] == "Wale Warden"
    bad_warden = await client.post(
        HO, headers=bearer(d), json={"name": "X House", "kind": "boys", "warden_id": s1}
    )
    assert bad_warden.status_code == 422
    assert (
        await client.post(HO, headers=bearer(teacher), json={"name": "Y", "kind": "boys"})
    ).status_code == 403
    assert (await client.get(HO, headers=bearer(parent))).status_code == 403
    rooms = {}
    for name, beds in (("Room 1", 2), ("Room 2", 1)):
        r = await client.post(
            f"{HO}/{hid}/rooms", headers=bearer(d), json={"name": name, "capacity": beds}
        )
        assert r.status_code == 201, r.text
        rooms[name] = r.json()["id"]
    dup = await client.post(
        f"{HO}/{hid}/rooms", headers=bearer(d), json={"name": "room 1", "capacity": 2}
    )
    assert dup.json()["code"] == "name_taken"

    # Boarders get rooms and beds; beds and rooms can't be overfilled.
    async def allocate(student: str, room: str, bed: str | None) -> httpx.Response:
        return await client.put(
            f"{HO}/boarders/{student}", headers=bearer(d), json={"room_id": room, "bed": bed}
        )

    assert (await allocate(s1, rooms["Room 1"], "1A")).status_code == 200
    assert (await allocate(s2, rooms["Room 1"], "1A")).json()["code"] == "bed_taken"
    assert (await allocate(s2, rooms["Room 1"], "1B")).status_code == 200
    assert (await allocate(s3, rooms["Room 1"], None)).json()["code"] == "room_full"
    assert (await allocate(s3, rooms["Room 2"], None)).status_code == 200
    listed = (await client.get(HO, headers=bearer(d))).json()
    assert [(h["beds"], h["boarders"]) for h in listed] == [(3, 3)]

    mine = (await client.get(f"{HO}/boarders/{s1}", headers=bearer(parent))).json()
    assert (mine["hostel_name"], mine["room_name"], mine["bed"], mine["warden_name"]) == (
        "Queen Amina House",
        "Room 1",
        "1A",
        "Wale Warden",
    )
    assert mine["exeat"] is None
    assert (await client.get(f"{HO}/boarders/{s2}", headers=bearer(parent))).status_code == 403

    # A parent asks for an exeat; the warden is told; someone else decides.
    ask = {
        "reason": "Grandmother's 80th birthday",
        "collector_name": "Ngozi Obi",
        "leave_at": _when(hours=1),
        "return_by": _when(days=2),
    }
    backwards = await client.post(
        f"{HO}/boarders/{s1}/exeats",
        headers=bearer(parent),
        json={**ask, "return_by": _when(minutes=30)},
    )
    assert backwards.status_code == 422
    r = await client.post(f"{HO}/boarders/{s1}/exeats", headers=bearer(parent), json=ask)
    assert r.status_code == 201, r.text
    exeat = r.json()
    assert (exeat["status"], exeat["requested_by_name"]) == ("pending", "Ngozi Obi")
    again = await client.post(f"{HO}/boarders/{s1}/exeats", headers=bearer(parent), json=ask)
    assert again.json()["code"] == "exeat_open"
    not_mine = await client.post(f"{HO}/boarders/{s2}/exeats", headers=bearer(parent), json=ask)
    assert not_mine.status_code == 403
    warden_notes = await _notices(client, warden)
    assert [(n["title"], n["link_id"]) for n in warden_notes] == [
        ("Exeat request for Ada Obi", exeat["id"])
    ]
    eid = exeat["id"]
    assert (
        await client.post(f"{HO}/exeats/{eid}/approve", headers=bearer(parent), json={})
    ).status_code == 403
    assert (await client.post(f"{HO}/exeats/{eid}/sign-out", headers=bearer(d))).json()[
        "code"
    ] == "exeat_not_approved"
    ok = await client.post(
        f"{HO}/exeats/{eid}/approve", headers=bearer(warden), json={"note": "Enjoy"}
    )
    assert ok.json()["status"] == "approved"
    out = await client.post(f"{HO}/exeats/{eid}/sign-out", headers=bearer(d))
    assert out.json()["status"] == "out"
    roll = (await client.get(f"{HO}/{hid}/residents", headers=bearer(warden))).json()
    assert [(x["student_name"], x["bed"], x["away"]) for x in roll] == [
        ("Ada Obi", "1A", True),
        ("Bola Ade", "1B", False),
        ("Chidi Okeke", None, False),
    ]
    assert (await client.delete(f"{HO}/boarders/{s1}", headers=bearer(d))).json()[
        "code"
    ] == "exeat_open"
    assert (await client.get(f"{HO}/exeats", headers=bearer(d), params={"overdue": True})).json()[
        "items"
    ] == []
    back = await client.post(f"{HO}/exeats/{eid}/sign-in", headers=bearer(d))
    assert (back.json()["status"], back.json()["returned_late"]) == ("returned", False)
    titles = [n["title"] for n in await _notices(client, parent)]
    assert titles == [
        "Ada is back at the hostel",
        "Ada has left the hostel",
        "Exeat approved for Ada",
    ]

    # Staff raise one: they can't approve it themselves; turning it down needs a reason.
    staff_ask = {**ask, "collector_name": "Chief Okeke"}
    raised = await client.post(f"{HO}/boarders/{s3}/exeats", headers=bearer(d), json=staff_ask)
    rid = raised.json()["id"]
    own = await client.post(f"{HO}/exeats/{rid}/approve", headers=bearer(d), json={})
    assert own.json()["code"] == "self_approval"
    blank = await client.post(f"{HO}/exeats/{rid}/reject", headers=bearer(warden), json={})
    assert blank.status_code == 422
    no = await client.post(
        f"{HO}/exeats/{rid}/reject", headers=bearer(warden), json={"note": "Exams on Monday"}
    )
    assert (no.json()["status"], no.json()["decision_note"]) == ("rejected", "Exams on Monday")

    # The parent cancels a fresh one before it starts.
    fresh = await client.post(f"{HO}/boarders/{s1}/exeats", headers=bearer(parent), json=ask)
    cancelled = await client.post(
        f"{HO}/exeats/{fresh.json()['id']}/cancel", headers=bearer(parent)
    )
    assert cancelled.json()["status"] == "cancelled"
    history = await client.get(f"{HO}/exeats", headers=bearer(parent), params={"student_id": s1})
    assert [e["status"] for e in history.json()["items"]] == ["cancelled", "returned"]
    assert (await client.get(f"{HO}/exeats", headers=bearer(parent))).status_code == 403

    # Overdue: a boarder still out after the return time.
    late: dict[str, Any] = (
        await client.post(
            f"{HO}/boarders/{s2}/exeats",
            headers=bearer(d),
            json={**staff_ask, "leave_at": _when(), "return_by": _when(hours=1)},
        )
    ).json()
    await client.post(f"{HO}/exeats/{late['id']}/approve", headers=bearer(warden), json={})
    await client.post(f"{HO}/exeats/{late['id']}/sign-out", headers=bearer(d))
    conn = await asyncpg.connect(owner_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.rls_bypass', 'on', true)")
            await conn.execute(
                "UPDATE hostel_exeats SET leave_at = now() - interval '3 hours',"
                " return_by = now() - interval '1 hour' WHERE id = $1",
                late["id"],
            )
    finally:
        await conn.close()
    overdue = (await client.get(f"{HO}/exeats", headers=bearer(d), params={"overdue": True})).json()
    assert [(e["id"], e["overdue"]) for e in overdue["items"]] == [(late["id"], True)]
    returned = await client.post(f"{HO}/exeats/{late['id']}/sign-in", headers=bearer(d))
    assert returned.json()["returned_late"] is True

    # Guard rails.
    shrink = await client.put(
        f"{HO}/rooms/{rooms['Room 1']}", headers=bearer(d), json={"name": "Room 1", "capacity": 1}
    )
    assert shrink.json()["code"] == "over_capacity"
    close = await client.put(
        f"{HO}/{hid}", headers=bearer(d), json={"name": "Queen Amina House", "active": False}
    )
    assert close.json()["code"] == "hostel_in_use"
    assert (await client.delete(f"{HO}/boarders/{s3}", headers=bearer(d))).status_code == 204
    assert (await client.get(f"{HO}/boarders/{s3}", headers=bearer(d))).json() is None

    conn = await asyncpg.connect(app_dsn)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("DELETE FROM hostel_exeats")
    finally:
        await conn.close()
    log = await client.get("/api/v1/audit-events", headers=bearer(d), params={"action": "hostel."})
    assert {e["action"] for e in log.json()["items"]} >= {
        "hostel.created",
        "hostel.room_added",
        "hostel.boarder_allocated",
        "hostel.boarder_vacated",
        "hostel.exeat_requested",
        "hostel.exeat_approved",
        "hostel.exeat_rejected",
        "hostel.exeat_cancelled",
        "hostel.exeat_signed_out",
        "hostel.exeat_returned",
    }
