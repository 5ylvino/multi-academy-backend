"""School sections: nursery, primary and secondary kept apart, with their own heads, staff,
data and features."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.infrastructure.container import Container
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer, register_and_verify, section_id
from tests.integration.test_finance_flows import FIN, _fee, _member, _school
from tests.integration.test_payment_proofs_flows import _parent

pytestmark = pytest.mark.integration

SEC = "/api/v1/academics/sections"
ACA = "/api/v1/academics"
AS = "/api/v1/assessment"
ALL_FEATURES = [
    "attendance",
    "gradebook",
    "report_cards",
    "clinic",
    "gate",
    "transport",
    "boarding",
]


async def _sections(client: httpx.AsyncClient, who: dict[str, Any]) -> list[dict[str, Any]]:
    r = await client.get(SEC, headers=bearer(who))
    assert r.status_code == 200, r.text
    found: list[dict[str, Any]] = r.json()
    return found


async def test_onboarding_choices(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    levels = ["nursery", "primary", "secondary"]
    merged = await register_and_verify(
        client, container, mailbox, slug="merged", email="h@merged.ng", school_levels=levels
    )
    assert [(s["name"], s["levels"]) for s in await _sections(client, merged)] == [
        ("Nursery and Primary", ["nursery", "primary"]),
        ("Secondary", ["secondary"]),
    ]
    apart = await register_and_verify(
        client,
        container,
        mailbox,
        slug="apart",
        email="h@apart.ng",
        school_levels=levels,
        nursery_with_primary=False,
        per_section_roles=["bursar", "nurse"],
    )
    found = await _sections(client, apart)
    assert [s["levels"] for s in found] == [["nursery"], ["primary"], ["secondary"]]
    # Boarding starts on only where there is secondary.
    assert ["boarding" in s["features"] for s in found] == [False, False, True]
    coverage = await client.get(f"{SEC}/staff-coverage", headers=bearer(apart))
    assert coverage.json() == {"per_section_roles": ["bursar", "nurse"]}

    only = await register_and_verify(
        client, container, mailbox, slug="kiddies", email="h@kid.ng", school_levels=["nursery"]
    )
    assert [s["name"] for s in await _sections(client, only)] == ["Nursery"]
    boot = (await client.get("/api/v1/bootstrap", headers=bearer(only))).json()
    assert boot["whole_school"] is True
    assert [s["name"] for s in boot["sections"]] == ["Nursery"]


async def test_owner_arrangements_are_checked(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    for bad, code in (
        ([{"levels": ["primary"]}, {"levels": ["primary", "secondary"]}], "level_in_two_sections"),
        ([{"levels": ["primary"]}], "level_without_section"),
        (
            [{"levels": ["primary"]}, {"levels": ["secondary"]}, {"levels": ["nursery"]}],
            "level_not_offered",
        ),
    ):
        r = await client.post(
            "/api/v1/schools",
            json={
                "school_name": "Bad Academy",
                "slug": "badacademy",
                "school_levels": ["primary", "secondary"],
                "sections": bad,
                "admin_full_name": "Ngozi Eze",
                "admin_email": "h@bad.ng",
                "admin_password": "Strong-Passphrase-2026",
            },
        )
        assert r.json()["code"] == code, r.text
    one = await register_and_verify(
        client,
        container,
        mailbox,
        slug="onehead",
        email="h@onehead.ng",
        school_levels=["primary", "secondary"],
        sections=[{"name": "Whole School", "levels": ["primary", "secondary"]}],
    )
    assert [s["name"] for s in await _sections(client, one)] == ["Whole School"]


async def test_heads_and_staff_see_only_their_section(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _school(client, container, mailbox, "split")
    d = sc["director"]
    p3, jss1 = sc["classes"]["P3"], sc["classes"]["JSS1"]
    head = await _member(
        client,
        container,
        mailbox,
        d,
        slug="split",
        email="ht@split.ng",
        role="head_teacher",
        name="Hana Head",
    )
    principal = await _member(
        client,
        container,
        mailbox,
        d,
        slug="split",
        email="p@split.ng",
        role="principal",
        name="Pat Principal",
    )
    parent = await _parent(client, container, mailbox, sc, "split")

    # A head must fit the section.
    for role, level in (("principal", "primary"), ("head_teacher", "secondary")):
        wrong = await client.post(
            "/api/v1/users",
            headers=bearer(d),
            json={
                "email": f"{role}@x.ng",
                "full_name": "Wrong Fit",
                "roles": [role],
                "section_ids": [await section_id(client, d, level)],
            },
        )
        assert wrong.json()["code"] == "role_section_mismatch", wrong.text
    missing = await client.post(
        "/api/v1/users",
        headers=bearer(d),
        json={"email": "t2@x.ng", "full_name": "No Section", "roles": ["subject_teacher"]},
    )
    assert missing.json()["code"] == "section_required"

    # Classes, rosters and subjects.
    async def classes(who: dict[str, Any]) -> list[str]:
        r = await client.get(f"{ACA}/classes", headers=bearer(who))
        return [c["code"] for c in r.json()]

    assert await classes(d) == ["P3", "JSS1"]
    assert await classes(head) == ["P3"]
    assert await classes(principal) == ["JSS1"]
    assert (await client.get(f"{ACA}/classes/{jss1}/roster", headers=bearer(head))).json()[
        "code"
    ] == "other_section"

    # People: the head sees their teachers, students and families, and whole-school staff.
    async def people(who: dict[str, Any]) -> set[str]:
        r = await client.get("/api/v1/users", headers=bearer(who), params={"limit": 100})
        assert r.status_code == 200, r.text
        return {u["full_name"] for u in r.json()["items"]}

    seen = await people(head)
    assert {"Ada Obi", "Bola Ade", "Tunde Teacher", "Ngozi Obi", "Bisi Bursar"} <= seen
    assert not {"Chidi Okeke", "Pat Principal"} & seen
    assert {"Chidi Okeke", "Pat Principal", "Ada Obi"} <= await people(d)

    # Announcements from a head reach their own section only.
    sent = await client.post(
        "/api/v1/announcements",
        headers=bearer(head),
        json={"title": "Sports day", "body": "Friday.", "roles": ["parent", "student"]},
    )
    assert sent.status_code == 201, sent.text
    assert sent.json()["class_ids"] == [p3]
    reach = await client.post(
        "/api/v1/announcements",
        headers=bearer(head),
        json={"title": "Exams", "body": "Soon.", "roles": ["parent"], "class_ids": [jss1]},
    )
    assert reach.json()["code"] == "other_section"
    feed = await client.get("/api/v1/announcements", headers=bearer(sc["s3"]))
    assert [a["title"] for a in feed.json()["items"]] == []
    feed = await client.get("/api/v1/announcements", headers=bearer(parent))
    assert [a["title"] for a in feed.json()["items"]] == ["Sports day"]

    # Bootstrap tells the app which sections to show.
    boot = (await client.get("/api/v1/bootstrap", headers=bearer(head))).json()
    primary = await section_id(client, d, "primary")
    assert (boot["whole_school"], boot["my_section_ids"]) == (False, [primary])
    mine = (await client.get(f"{SEC}/mine", headers=bearer(principal))).json()
    assert [s["name"] for s in mine["sections"]] == ["Secondary"]
    listed = {s["name"]: s for s in await _sections(client, d)}
    assert [h["full_name"] for h in listed["Primary"]["heads"]] == ["Hana Head"]
    assert [h["full_name"] for h in listed["Secondary"]["heads"]] == ["Pat Principal"]


async def test_per_section_bursar_and_features(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _school(client, container, mailbox, "persec")
    d = sc["director"]
    s1, s3 = sc["s1"], sc["s3"]
    primary = await section_id(client, d, "primary")

    # The owner moves the bursar to per-section working; the existing bursar must be placed.
    moved = await client.put(
        f"{SEC}/staff-coverage", headers=bearer(d), json={"per_section_roles": ["bursar"]}
    )
    assert moved.json() == {"per_section_roles": ["bursar"]}
    bursar = sc["bursar"]
    assert (
        await client.get(f"{FIN}/students/{s1['user_id']}/statement", headers=bearer(bursar))
    ).status_code == 403
    placed = await client.put(
        f"/api/v1/users/{bursar['user_id']}/sections",
        headers=bearer(d),
        json={"section_ids": [primary]},
    )
    assert placed.status_code == 200, placed.text
    ok = await client.get(f"{FIN}/students/{s1['user_id']}/statement", headers=bearer(bursar))
    assert ok.status_code == 200
    other = await client.get(f"{FIN}/students/{s3['user_id']}/statement", headers=bearer(bursar))
    assert other.json()["code"] == "other_section"
    await _fee(client, d, name="Tuition", amount_minor=10_000_000)
    run = await client.post(f"{FIN}/billing-runs", headers=bearer(bursar), json={})
    assert run.json()["invoiced"] == 2  # Ada and Bola in primary; not Chidi in secondary
    # A whole-school role can't be put in a section.
    nurse = await _member(
        client,
        container,
        mailbox,
        d,
        slug="persec",
        email="n@persec.ng",
        role="nurse",
        name="Nkem Nurse",
    )
    refused = await client.put(
        f"/api/v1/users/{nurse['user_id']}/sections",
        headers=bearer(d),
        json={"section_ids": [primary]},
    )
    assert refused.json()["code"] == "whole_school_staff"

    # Features per section: primary stops using the gradebook.
    off = await client.put(
        f"{SEC}/{primary}/features",
        headers=bearer(d),
        json={"features": [f for f in ALL_FEATURES if f not in {"gradebook", "boarding"}]},
    )
    assert "gradebook" not in {s["name"]: s for s in off.json()}["Primary"]["features"]
    subject = await client.post(
        f"{ACA}/subjects",
        headers=bearer(d),
        json={"name": "Mathematics", "code": "MTH", "school_level": "primary"},
    )
    await client.put(
        f"{ACA}/classes/{sc['classes']['P3']}/subjects/{subject.json()['id']}",
        headers=bearer(d),
        json={"teacher_id": None},
    )
    sheet = await client.get(
        f"{AS}/sheet",
        headers=bearer(d),
        params={
            "term_id": sc["term_id"],
            "class_id": sc["classes"]["P3"],
            "subject_id": subject.json()["id"],
        },
    )
    assert sheet.json()["code"] == "section_feature_off"


async def test_rearranging_sections(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    d = await register_and_verify(
        client,
        container,
        mailbox,
        slug="rearr",
        email="h@rearr.ng",
        school_levels=["nursery", "primary", "secondary"],
    )
    head = await _member(
        client,
        container,
        mailbox,
        d,
        slug="rearr",
        email="ht@rearr.ng",
        role="head_teacher",
        name="Hana Head",
    )
    basic = await section_id(client, d, "primary")
    secondary = await section_id(client, d, "secondary")

    # Nursery leaves primary and gets its own section; the head stays with primary.
    split = await client.put(
        SEC,
        headers=bearer(d),
        json={
            "sections": [
                {"name": "Nursery", "levels": ["nursery"]},
                {"id": basic, "levels": ["primary"]},
                {"id": secondary, "levels": ["secondary"]},
            ]
        },
    )
    assert split.status_code == 200, split.text
    by_name = {s["name"]: s for s in split.json()}
    assert set(by_name) == {"Nursery", "Primary", "Secondary"}
    assert by_name["Primary"]["id"] == basic
    assert [h["full_name"] for h in by_name["Primary"]["heads"]] == ["Hana Head"]
    assert by_name["Nursery"]["staff_count"] == 0

    # Merged back: the nursery section goes and its levels join primary.
    merged = await client.put(
        SEC,
        headers=bearer(d),
        json={
            "sections": [
                {"id": basic, "levels": ["nursery", "primary"]},
                {"id": secondary, "levels": ["secondary"]},
            ]
        },
    )
    assert [s["name"] for s in merged.json()] == ["Nursery and Primary", "Secondary"]
    # A principal can't be left heading a section without secondary.
    clash = await client.put(
        SEC,
        headers=bearer(d),
        json={
            "sections": [
                {"id": basic, "levels": ["nursery", "primary", "secondary"]},
                {"id": secondary, "levels": []},
            ]
        },
    )
    assert clash.status_code == 422
    assert (await client.put(SEC, headers=bearer(head), json={"sections": []})).status_code in (
        403,
        422,
    )
    log = await client.get(
        "/api/v1/audit-events", headers=bearer(d), params={"action": "academics.sections"}
    )
    assert [e["action"] for e in log.json()["items"]] == [
        "academics.sections_arranged",
        "academics.sections_arranged",
    ]
