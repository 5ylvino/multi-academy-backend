"""Assessment scheme, grading scale and the gradebook, end to end over HTTP."""

from __future__ import annotations

from typing import Any

import asyncpg
import httpx
import pytest

from app.infrastructure.container import Container
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer
from tests.integration.test_clinic_and_gate import _notices
from tests.integration.test_finance_flows import _member, _school

pytestmark = pytest.mark.integration

AS = "/api/v1/assessment"
ACA = "/api/v1/academics"


async def _gradebook_school(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, slug: str
) -> dict[str, Any]:
    """P3 (Ada, Bola) with a class teacher, Mathematics taught by a subject teacher, English
    with no teacher, and two approvers."""
    sc = await _school(client, container, mailbox, slug)
    d = sc["director"]
    for key, role, name in (
        ("maths", "subject_teacher", "Sola Subject"),
        ("principal", "principal", "Pat Principal"),
        ("vice", "vice_principal", "Vic Vice"),
    ):
        sc[key] = await _member(
            client,
            container,
            mailbox,
            d,
            slug=slug,
            email=f"{key}@{slug}.ng",
            role=role,
            name=name,
        )
    p3 = sc["classes"]["P3"]
    r = await client.patch(
        f"{ACA}/classes/{p3}",
        headers=bearer(d),
        json={"class_teacher_id": sc["teacher"]["user_id"]},
    )
    assert r.status_code == 200, r.text
    subjects = {}
    for code, name, teacher in (("MTH", "Mathematics", sc["maths"]), ("ENG", "English", None)):
        s = await client.post(
            f"{ACA}/subjects",
            headers=bearer(d),
            json={"name": name, "code": code, "school_level": "primary"},
        )
        assert s.status_code == 201, s.text
        subjects[code] = s.json()["id"]
        link = await client.put(
            f"{ACA}/classes/{p3}/subjects/{subjects[code]}",
            headers=bearer(d),
            json={"teacher_id": teacher["user_id"] if teacher else None},
        )
        assert link.status_code == 200, link.text
    sc["subjects"] = subjects
    return sc


async def test_scheme_and_scale(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox
) -> None:
    sc = await _gradebook_school(client, container, mailbox, "scheme")
    d, maths = sc["director"], sc["maths"]
    default = (await client.get(f"{AS}/schemes/primary", headers=bearer(maths))).json()
    assert [(c["code"], c["max_score"]) for c in default["components"]] == [
        ("note", 10),
        ("test_1", 15),
        ("test_2", 15),
        ("exam", 60),
    ]
    assert default["locked"] is False
    parts = [
        {"code": "ca_1", "name": "First CA", "kind": "continuous", "max_score": 20},
        {"code": "ca_2", "name": "Second CA", "kind": "continuous", "max_score": 20},
        {"code": "exam", "name": "Examination", "kind": "exam", "max_score": 60},
    ]
    assert (
        await client.put(f"{AS}/schemes/primary", headers=bearer(maths), json={"components": parts})
    ).status_code == 403
    short = await client.put(
        f"{AS}/schemes/primary", headers=bearer(d), json={"components": parts[:2]}
    )
    assert short.json()["code"] == "scheme_not_100"
    saved = await client.put(f"{AS}/schemes/primary", headers=bearer(d), json={"components": parts})
    assert saved.status_code == 200, saved.text
    assert [c["code"] for c in saved.json()["components"]] == ["ca_1", "ca_2", "exam"]
    assert (await client.get(f"{AS}/schemes/secondary", headers=bearer(d))).json()["components"]

    scale = (await client.get(f"{AS}/scales/primary", headers=bearer(sc["s1"]))).json()
    assert scale[0] == {"grade": "A", "remark": "Distinction", "min_score": 70, "max_score": 100}
    gap = [
        {"grade": "A", "remark": "Excellent", "min_score": 75, "max_score": 100},
        {"grade": "F", "remark": "Fail", "min_score": 0, "max_score": 70},
    ]
    assert (
        await client.put(f"{AS}/scales/primary", headers=bearer(d), json={"bands": gap})
    ).json()["code"] == "scale_gap"
    ok = [
        {"grade": "f", "remark": "Fail", "min_score": 0, "max_score": 49},
        {"grade": "p", "remark": "Pass", "min_score": 50, "max_score": 74},
        {"grade": "a", "remark": "Excellent", "min_score": 75, "max_score": 100},
    ]
    stored = await client.put(f"{AS}/scales/primary", headers=bearer(d), json={"bands": ok})
    assert [b["grade"] for b in stored.json()] == ["A", "P", "F"]
    log = await client.get(
        "/api/v1/audit-events", headers=bearer(d), params={"action": "assessment."}
    )
    assert {e["action"] for e in log.json()["items"]} == {
        "assessment.scheme_changed",
        "assessment.scale_changed",
    }


async def test_marks_to_approval(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, app_dsn: str
) -> None:
    sc = await _gradebook_school(client, container, mailbox, "marks")
    maths, principal, vice, teacher = sc["maths"], sc["principal"], sc["vice"], sc["teacher"]
    scope = {
        "term_id": sc["term_id"],
        "class_id": sc["classes"]["P3"],
        "subject_id": sc["subjects"]["MTH"],
    }
    s1, s2, s3 = (sc[k]["user_id"] for k in ("s1", "s2", "s3"))

    first = await client.get(f"{AS}/sheet", headers=bearer(maths), params=scope)
    assert first.status_code == 200, first.text
    view = first.json()
    assert view["id"] is None
    assert (view["teacher_name"], view["can_edit"], view["missing"]) == ("Sola Subject", True, 8)
    assert [s["student_name"] for s in view["students"]] == ["Ada Obi", "Bola Ade"]
    parts = {c["code"]: c["id"] for c in view["components"]}
    homeroom = (await client.get(f"{AS}/sheet", headers=bearer(teacher), params=scope)).json()
    assert homeroom["can_edit"] is False
    assert (
        await client.get(f"{AS}/sheet", headers=bearer(sc["bursar"]), params=scope)
    ).status_code == 403

    async def save(who: dict[str, Any], *entries: tuple[str, str, float | None]) -> httpx.Response:
        return await client.put(
            f"{AS}/sheet/marks",
            headers=bearer(who),
            json={
                **scope,
                "entries": [
                    {"student_id": s, "component_id": parts[c], "score": v} for s, c, v in entries
                ],
            },
        )

    assert (await save(maths, (s1, "test_1", 16))).json()["code"] == "score_out_of_range"
    assert (await save(maths, (s3, "exam", 50))).json()["code"] == "not_in_class"
    assert (await save(teacher, (s1, "exam", 50))).status_code == 403
    r = await save(
        maths,
        (s1, "note", 8),
        (s1, "test_1", 12.5),
        (s1, "test_2", 14),
        (s1, "exam", 51),
        (s2, "note", 5),
        (s2, "exam", 30),
    )
    assert r.status_code == 200, r.text
    sheet = r.json()
    sheet_id = sheet["id"]
    ada, bola = sheet["students"]
    assert (ada["total"], ada["missing"]) == (85.5, 0)
    assert (bola["total"], bola["missing"]) == (None, 2)
    cleared = await save(maths, (s2, "exam", None))
    assert cleared.json()["students"][1]["missing"] == 3

    # The scheme is now fixed for the term.
    locked = await client.put(
        f"{AS}/schemes/primary",
        headers=bearer(sc["director"]),
        json={"components": [{"code": "exam", "name": "Exam", "kind": "exam", "max_score": 100}]},
    )
    assert locked.json()["code"] == "scheme_in_use"

    # Incomplete sheets can't be submitted; a student who doesn't take it is excused.
    incomplete = await client.post(f"{AS}/sheets/{sheet_id}/submit", headers=bearer(maths))
    assert incomplete.json()["code"] == "sheet_incomplete"
    excused = await client.put(
        f"{AS}/sheet/exemptions",
        headers=bearer(maths),
        json={**scope, "student_id": s2, "exempt": True},
    )
    excused_bola = excused.json()["students"][1]
    assert (excused_bola["exempt"], excused_bola["marks"]) == (True, {})
    assert excused.json()["missing"] == 0
    assert (await save(maths, (s2, "exam", 40))).json()["code"] == "student_exempt"
    submitted = await client.post(f"{AS}/sheets/{sheet_id}/submit", headers=bearer(maths))
    assert submitted.json()["status"] == "submitted"
    assert [n["title"] for n in await _notices(client, principal)] == [
        "Mathematics marks for P3 are ready"
    ]
    assert (await save(maths, (s1, "exam", 52))).json()["code"] == "sheet_submitted"
    waiting = (
        await client.get(f"{AS}/terms/{sc['term_id']}/awaiting-approval", headers=bearer(principal))
    ).json()
    assert [(w["subject_name"], w["status"]) for w in waiting] == [("Mathematics", "submitted")]

    # The database refuses changes to marks on a sheet that isn't a draft.
    conn = await asyncpg.connect(app_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.rls_bypass', 'on', true)")
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute("UPDATE assessment_marks SET score = 0")
    finally:
        await conn.close()

    # Sent back, fixed, approved by someone else, then reopened.
    assert (
        await client.post(f"{AS}/sheets/{sheet_id}/approve", headers=bearer(maths))
    ).status_code == 403
    back = await client.post(
        f"{AS}/sheets/{sheet_id}/send-back",
        headers=bearer(principal),
        json={"note": "Bola does take maths. Please add her marks."},
    )
    assert (back.json()["status"], back.json()["note"]) == (
        "draft",
        "Bola does take maths. Please add her marks.",
    )
    assert [n["title"] for n in await _notices(client, maths)] == [
        "Mathematics marks for P3 sent back"
    ]
    await client.put(
        f"{AS}/sheet/exemptions",
        headers=bearer(maths),
        json={**scope, "student_id": s2, "exempt": False},
    )
    done = await save(
        maths, (s2, "note", 6), (s2, "test_1", 9), (s2, "test_2", 10), (s2, "exam", 33)
    )
    assert done.json()["missing"] == 0
    await client.post(f"{AS}/sheets/{sheet_id}/submit", headers=bearer(maths))
    approved = await client.post(f"{AS}/sheets/{sheet_id}/approve", headers=bearer(principal))
    assert approved.json()["status"] == "approved"
    assert (await save(principal, (s1, "exam", 52))).json()["code"] == "sheet_approved"
    blank = await client.post(
        f"{AS}/sheets/{sheet_id}/reopen", headers=bearer(vice), json={"note": " "}
    )
    assert blank.status_code == 422
    reopened = await client.post(
        f"{AS}/sheets/{sheet_id}/reopen", headers=bearer(vice), json={"note": "Exam remarked"}
    )
    assert reopened.json()["status"] == "draft"

    # A leader who submits a sheet can't approve it too.
    eng = {**scope, "subject_id": sc["subjects"]["ENG"]}
    eng_parts = parts  # same level, same scheme
    r = await client.put(
        f"{AS}/sheet/marks",
        headers=bearer(principal),
        json={
            **eng,
            "entries": [
                {"student_id": s, "component_id": eng_parts[c], "score": 10}
                for s in (s1, s2)
                for c in ("note", "test_1", "test_2", "exam")
            ],
        },
    )
    assert r.status_code == 200, r.text
    eng_id = r.json()["id"]
    await client.post(f"{AS}/sheets/{eng_id}/submit", headers=bearer(principal))
    own = await client.post(f"{AS}/sheets/{eng_id}/approve", headers=bearer(principal))
    assert own.json()["code"] == "self_approval"
    assert (await client.post(f"{AS}/sheets/{eng_id}/approve", headers=bearer(vice))).json()[
        "status"
    ] == "approved"

    # Overviews for the class teacher and the subject teacher.
    overview = await client.get(
        f"{AS}/terms/{sc['term_id']}/classes/{scope['class_id']}/sheets", headers=bearer(teacher)
    )
    assert [(o["subject_name"], o["status"], o["missing"]) for o in overview.json()] == [
        ("English", "approved", 0),
        ("Mathematics", "draft", 0),
    ]
    mine = (await client.get(f"{AS}/terms/{sc['term_id']}/my-sheets", headers=bearer(maths))).json()
    assert [(m["class_name"], m["subject_name"]) for m in mine] == [("P3", "Mathematics")]

    log = await client.get(
        "/api/v1/audit-events", headers=bearer(sc["director"]), params={"action": "assessment."}
    )
    events = log.json()["items"]
    assert {e["action"] for e in events} >= {
        "assessment.marks_saved",
        "assessment.student_exempted",
        "assessment.exemption_removed",
        "assessment.sheet_submitted",
        "assessment.sheet_sent_back",
        "assessment.sheet_approved",
        "assessment.sheet_reopened",
    }
