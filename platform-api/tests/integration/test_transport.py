"""School transport, end to end over HTTP."""

from __future__ import annotations

import asyncpg
import httpx
import pytest

from app.infrastructure.container import Container
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer
from tests.integration.test_clinic_and_gate import _notices, _setup
from tests.integration.test_finance_flows import _member

pytestmark = pytest.mark.integration

TR = "/api/v1/transport"


async def test_routes_riders_and_boarding(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, app_dsn: str
) -> None:
    sc = await _setup(client, container, mailbox, "bus")
    d, teacher, parent = sc["director"], sc["teacher"], sc["parent"]
    s1, s2, s3 = (sc[k]["user_id"] for k in ("s1", "s2", "s3"))
    drivers = [
        await _member(
            client,
            container,
            mailbox,
            d,
            slug="bus",
            email=f"{key}@bus.ng",
            role="driver",
            name=name,
        )
        for key, name in (("driver", "Dauda Driver"), ("other", "Olu Driver"))
    ]
    driver, other = drivers

    # The office sets up a route with a driver and two stops.
    body = {"name": "Lekki", "code": "r1", "vehicle_plate": "lnd 123 ab", "capacity": 2}
    not_driver = await client.post(
        f"{TR}/routes", headers=bearer(d), json={**body, "driver_id": teacher["user_id"]}
    )
    assert not_driver.status_code == 422
    made = await client.post(
        f"{TR}/routes", headers=bearer(d), json={**body, "driver_id": driver["user_id"]}
    )
    assert made.status_code == 201, made.text
    route = made.json()
    assert (route["code"], route["vehicle_plate"], route["driver_name"]) == (
        "R1",
        "LND 123 AB",
        "Dauda Driver",
    )
    dup = await client.post(f"{TR}/routes", headers=bearer(d), json=body)
    assert dup.json()["code"] == "code_taken"
    assert (
        await client.post(f"{TR}/routes", headers=bearer(teacher), json=body)
    ).status_code == 403
    rid = route["id"]
    stops = []
    for name, pos, am in (("Admiralty Way", 1, "06:45"), ("Chevron Drive", 2, "07:05")):
        r = await client.post(
            f"{TR}/routes/{rid}/stops",
            headers=bearer(d),
            json={"name": name, "position": pos, "morning_at": am, "afternoon_at": "15:30"},
        )
        assert r.status_code == 201, r.text
        stops.append(r.json()["id"])
    first, second = stops

    # Students on the route; seats are checked, moving within the route is fine.
    for student, stop in ((s1, second), (s2, second)):
        r = await client.put(
            f"{TR}/students/{student}", headers=bearer(d), json={"route_id": rid, "stop_id": stop}
        )
        assert r.status_code == 200, r.text
    full = await client.put(
        f"{TR}/students/{s3}", headers=bearer(d), json={"route_id": rid, "stop_id": first}
    )
    assert full.json()["code"] == "route_full"
    moved = await client.put(
        f"{TR}/students/{s1}", headers=bearer(d), json={"route_id": rid, "stop_id": first}
    )
    assert moved.json()["stop"]["name"] == "Admiralty Way"
    assert moved.json()["driver_name"] == "Dauda Driver"

    # Who sees routes.
    listed = (await client.get(f"{TR}/routes", headers=bearer(teacher))).json()
    assert [(r["code"], r["riders"], len(r["stops"])) for r in listed] == [("R1", 2, 2)]
    assert (await client.get(f"{TR}/routes", headers=bearer(parent))).status_code == 403
    assert [r["id"] for r in (await client.get(f"{TR}/routes", headers=bearer(driver))).json()] == [
        rid
    ]
    assert (await client.get(f"{TR}/routes", headers=bearer(other))).json() == []
    assert (await client.get(f"{TR}/routes/{rid}", headers=bearer(other))).status_code == 403

    # The morning run.
    async def board(who: dict[str, str], student: str, kind: str) -> httpx.Response:
        return await client.post(
            f"{TR}/routes/{rid}/boardings",
            headers=bearer(who),
            json={"student_id": student, "direction": "to_school", "kind": kind},
        )

    assert (await board(driver, s1, "dropped")).json()["code"] == "boarding_out_of_order"
    assert (await board(driver, s1, "boarded")).status_code == 201
    assert (await board(driver, s1, "boarded")).status_code == 409
    assert (await board(driver, s2, "absent")).status_code == 201
    assert (await board(driver, s2, "boarded")).status_code == 201  # caught the bus late
    assert (await board(driver, s1, "dropped")).status_code == 201
    assert (await board(driver, s3, "boarded")).json()["code"] == "not_on_route"
    assert (await board(other, s1, "dropped")).status_code == 403
    assert (await board(teacher, s1, "dropped")).status_code == 403

    m = await client.get(
        f"{TR}/routes/{rid}/manifest", headers=bearer(driver), params={"direction": "to_school"}
    )
    assert m.status_code == 200, m.text
    manifest = m.json()
    assert [(r["student_name"], r["stop_name"], r["status"]) for r in manifest["riders"]] == [
        ("Ada Obi", "Admiralty Way", "dropped"),
        ("Bola Ade", "Chevron Drive", "boarded"),
    ]
    assert manifest["on_board"] == 1
    home = await client.get(
        f"{TR}/routes/{rid}/manifest", headers=bearer(driver), params={"direction": "from_school"}
    )
    assert [r["student_name"] for r in home.json()["riders"]] == ["Bola Ade", "Ada Obi"]
    assert all(r["status"] is None for r in home.json()["riders"])

    # Ada's mother is told, sees today's trips and the history; not other children's.
    titles = [n["title"] for n in await _notices(client, parent)]
    assert titles == ["Ada has arrived at school", "Ada is on the bus to school"]
    mine = (await client.get(f"{TR}/students/{s1}", headers=bearer(parent))).json()
    assert mine["to_school"]["kind"] == "dropped"
    assert mine["from_school"] is None
    assert (await client.get(f"{TR}/students/{s2}", headers=bearer(parent))).status_code == 403
    assert (await client.get(f"{TR}/students/{s3}", headers=bearer(d))).json() is None
    trips = await client.get(f"{TR}/students/{s1}/boardings", headers=bearer(parent))
    assert [b["kind"] for b in trips.json()["items"]] == ["dropped", "boarded"]

    # Guard rails on changes.
    in_use = await client.delete(f"{TR}/stops/{second}", headers=bearer(d))
    assert in_use.json()["code"] == "stop_in_use"
    upd = {"name": "Lekki", "vehicle_plate": None, "capacity": 2, "driver_id": driver["user_id"]}
    off = await client.put(f"{TR}/routes/{rid}", headers=bearer(d), json={**upd, "active": False})
    assert off.json()["code"] == "route_in_use"
    small = await client.put(f"{TR}/routes/{rid}", headers=bearer(d), json={**upd, "capacity": 1})
    assert small.json()["code"] == "over_capacity"
    assert (await client.delete(f"{TR}/students/{s2}", headers=bearer(d))).status_code == 204
    assert (await client.delete(f"{TR}/students/{s2}", headers=bearer(d))).status_code == 404
    assert (await client.delete(f"{TR}/stops/{second}", headers=bearer(d))).status_code == 204
    after = (await client.get(f"{TR}/routes/{rid}", headers=bearer(d))).json()
    assert [s["name"] for s in after["stops"]] == ["Admiralty Way"]
    assert after["riders"] == 1

    # Boarding records can't be changed or removed by the app; every step is logged.
    conn = await asyncpg.connect(app_dsn)
    try:
        for sql in (
            "UPDATE transport_boardings SET kind = 'boarded'",
            "DELETE FROM transport_boardings",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(sql)
    finally:
        await conn.close()
    log = await client.get(
        "/api/v1/audit-events", headers=bearer(d), params={"action": "transport."}
    )
    assert {e["action"] for e in log.json()["items"]} >= {
        "transport.route_created",
        "transport.stop_added",
        "transport.stop_removed",
        "transport.student_assigned",
        "transport.student_unassigned",
        "transport.boarding_recorded",
    }
