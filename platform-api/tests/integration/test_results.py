"""Results, positions, report cards and release to families, end to end over HTTP."""

from __future__ import annotations

from typing import Any

import asyncpg
import httpx
import pytest

from app.infrastructure.container import Container
from app.infrastructure.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.use_cases.assessment.notifications import SendReportCardEmails
from tests.integration.conftest import Mailbox
from tests.integration.helpers import bearer
from tests.integration.test_clinic_and_gate import _notices, _Outbox
from tests.integration.test_gradebook import AS, _gradebook_school
from tests.integration.test_payment_proofs_flows import _parent

pytestmark = pytest.mark.integration

# Scheme: note 10, test_1 15, test_2 15, exam 60.
MARKS = {
    "MTH": {"s1": (8, 12, 14, 50), "s2": (6, 9, 10, 33)},  # Ada 84, Bola 58
    "ENG": {"s1": (5, 10, 10, 40), "s2": (9, 14, 14, 55)},  # Ada 65, Bola 92
}


async def _approved(
    client: httpx.AsyncClient,
    sc: dict[str, Any],
    code: str,
    *,
    enter: dict[str, Any],
    approve: dict[str, Any],
    marks: dict[str, tuple[int, int, int, int]] | None = None,
) -> str:
    scope = {
        "term_id": sc["term_id"],
        "class_id": sc["classes"]["P3"],
        "subject_id": sc["subjects"][code],
    }
    view = (await client.get(f"{AS}/sheet", headers=bearer(enter), params=scope)).json()
    parts = [c["id"] for c in view["components"]]
    entries = [
        {"student_id": sc[who]["user_id"], "component_id": part, "score": score}
        for who, scores in (marks or MARKS[code]).items()
        for part, score in zip(parts, scores, strict=True)
    ]
    saved = await client.put(
        f"{AS}/sheet/marks", headers=bearer(enter), json={**scope, "entries": entries}
    )
    assert saved.status_code == 200, saved.text
    sheet_id: str = saved.json()["id"]
    submitted = await client.post(f"{AS}/sheets/{sheet_id}/submit", headers=bearer(enter))
    assert submitted.status_code == 200, submitted.text
    ok = await client.post(f"{AS}/sheets/{sheet_id}/approve", headers=bearer(approve))
    assert ok.json()["status"] == "approved", ok.text
    return sheet_id


async def test_results_release_to_families(
    client: httpx.AsyncClient, container: Container, mailbox: Mailbox, app_dsn: str
) -> None:
    sc = await _gradebook_school(client, container, mailbox, "results")
    parent = await _parent(client, container, mailbox, sc, "results")
    principal, vice, teacher, maths = sc["principal"], sc["vice"], sc["teacher"], sc["maths"]
    term, p3 = sc["term_id"], sc["classes"]["P3"]
    ada, bola = sc["s1"]["user_id"], sc["s2"]["user_id"]
    board = f"{AS}/terms/{term}/classes/{p3}"
    ada_card = f"{AS}/students/{ada}/report-cards/{term}"

    # Nothing to release yet; only the class teacher and leaders may look.
    empty = await client.get(f"{board}/broadsheet", headers=bearer(teacher))
    assert empty.status_code == 200, empty.text
    assert {s["status"] for s in empty.json()["subjects"]} == {"no_marks"}
    assert empty.json()["ready_to_release"] is False
    assert (
        await client.get(f"{board}/broadsheet", headers=bearer(sc["bursar"]))
    ).status_code == 403
    assert (await client.get(f"{board}/broadsheet", headers=bearer(maths))).status_code == 403
    early = await client.post(f"{board}/release", headers=bearer(principal))
    assert early.json()["code"] == "not_ready"

    mth = await _approved(client, sc, "MTH", enter=maths, approve=principal)
    half = await client.post(f"{board}/release", headers=bearer(principal))
    assert half.json()["code"] == "not_ready"
    assert half.json()["details"]["subjects"] == ["English"]
    await _approved(client, sc, "ENG", enter=principal, approve=vice)

    preview = (await client.get(f"{board}/broadsheet", headers=bearer(teacher))).json()
    assert preview["ready_to_release"] is True
    assert preview["release_status"] is None
    first, second = preview["cards"]
    assert (first["student"]["name"], first["position"], first["average"]) == (
        "Bola Ade",
        1,
        "75.00",
    )
    assert (second["student"]["name"], second["position"], second["average"]) == (
        "Ada Obi",
        2,
        "74.50",
    )
    lines = {line["subject_name"]: line for line in second["subjects"]}
    assert (lines["Mathematics"]["total"], lines["Mathematics"]["grade"]) == ("84.00", "A")
    assert (lines["Mathematics"]["position"], lines["Mathematics"]["out_of"]) == (1, 2)
    assert (lines["English"]["grade"], lines["English"]["remark"]) == ("B", "Very good")
    assert second["released"] is False

    # Comments: the class teacher writes theirs, a leader writes the head's.
    note = {"teacher_comment": "Ada works hard and helps others."}
    assert (
        await client.put(f"{ada_card}/comments", headers=bearer(teacher), json=note)
    ).status_code == 204
    head = {"head_comment": "A fine term."}
    assert (
        await client.put(f"{ada_card}/comments", headers=bearer(teacher), json=head)
    ).status_code == 403
    assert (
        await client.put(f"{ada_card}/comments", headers=bearer(maths), json=note)
    ).status_code == 403
    assert (
        await client.put(f"{ada_card}/comments", headers=bearer(principal), json=head)
    ).status_code == 204
    assert (
        await client.put(f"{ada_card}/comments", headers=bearer(teacher), json={})
    ).status_code == 422

    # Families see nothing before release.
    hidden = await client.get(ada_card, headers=bearer(parent))
    assert (hidden.status_code, hidden.json()["code"]) == (404, "not_released")
    assert (
        await client.get(f"{AS}/students/{ada}/report-cards", headers=bearer(parent))
    ).json() == []

    assert (await client.post(f"{board}/release", headers=bearer(teacher))).status_code == 403
    released = await client.post(f"{board}/release", headers=bearer(principal))
    assert released.status_code == 200, released.text
    assert (released.json()["release_status"], released.json()["release_version"]) == (
        "released",
        1,
    )
    again = await client.post(f"{board}/release", headers=bearer(principal))
    assert again.json()["code"] == "already_released"

    card = (await client.get(ada_card, headers=bearer(parent))).json()
    assert (card["released"], card["version"], card["position"], card["out_of"]) == (True, 1, 2, 2)
    assert card["comments"] == {
        "class_teacher": "Ada works hard and helps others.",
        "head": "A fine term.",
    }
    assert card["class"]["class_teacher"] == "Tunde Teacher"
    assert (await client.get(ada_card, headers=bearer(sc["s1"]))).json()["position"] == 2
    other = await client.get(f"{AS}/students/{bola}/report-cards/{term}", headers=bearer(parent))
    assert other.status_code == 403
    listed = (await client.get(f"{AS}/students/{ada}/report-cards", headers=bearer(parent))).json()
    assert [(c["class_name"], c["position"], c["average"]) for c in listed] == [("P3", 2, "74.50")]
    titles = [n["title"] for n in await _notices(client, parent)]
    assert "First Term report card for Ada is ready" in titles

    await container.outbox.run_once()
    events = [p for n, p in mailbox.events if n == "assessment.results_released"]
    assert len(events) == 1
    assert set(events[0]["student_ids"]) == {ada, bola}
    box = _Outbox()
    handler = SendReportCardEmails(
        email=box,
        uow_factory=SqlAlchemyUnitOfWorkFactory(container.session_factory),
        school_app_url="https://app.example",
    )
    await handler(events[0])
    assert [m.to for m in box.sent] == ["parent@results.ng"]
    assert box.sent[0].subject == "Ada's First Term report card is ready"
    assert "74.50" not in box.sent[0].text

    # Released results freeze the marks and comments.
    locked = await client.post(
        f"{AS}/sheets/{mth}/reopen", headers=bearer(vice), json={"note": "Exam remarked"}
    )
    assert locked.json()["code"] == "results_released"
    late = await client.put(f"{ada_card}/comments", headers=bearer(teacher), json=note)
    assert late.json()["code"] == "results_released"

    # Withdrawn: hidden from families, no email goes out, marks can be corrected.
    assert (
        await client.post(f"{board}/withdraw", headers=bearer(principal), json={"reason": " "})
    ).status_code == 422
    pulled = await client.post(
        f"{board}/withdraw", headers=bearer(principal), json={"reason": "Maths exam remarked"}
    )
    assert pulled.json()["release_status"] == "withdrawn"
    assert (await client.get(ada_card, headers=bearer(parent))).status_code == 404
    assert (
        await client.get(f"{AS}/students/{ada}/report-cards", headers=bearer(parent))
    ).json() == []
    quiet = _Outbox()
    await SendReportCardEmails(
        email=quiet,
        uow_factory=SqlAlchemyUnitOfWorkFactory(container.session_factory),
        school_app_url="https://app.example",
    )(events[0])
    assert quiet.sent == []
    reopened = await client.post(
        f"{AS}/sheets/{mth}/reopen", headers=bearer(vice), json={"note": "Exam remarked"}
    )
    assert reopened.json()["status"] == "draft", reopened.text
    await _approved(
        client,
        sc,
        "MTH",
        enter=maths,
        approve=principal,
        marks={"s1": (8, 12, 14, 58), "s2": (6, 9, 10, 33)},
    )
    redo = await client.post(f"{board}/release", headers=bearer(principal))
    assert redo.json()["release_version"] == 2, redo.text
    fresh = (await client.get(ada_card, headers=bearer(parent))).json()
    assert (fresh["version"], fresh["position"], fresh["average"]) == (2, 1, "78.50")

    # The database never lets the app delete a released report card.
    conn = await asyncpg.connect(app_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.rls_bypass', 'on', true)")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute("DELETE FROM assessment_report_cards")
    finally:
        await conn.close()

    log = await client.get(
        "/api/v1/audit-events", headers=bearer(sc["director"]), params={"action": "assessment."}
    )
    actions = [e["action"] for e in log.json()["items"]]
    assert actions.count("assessment.results_released") == 2
    assert {"assessment.comment_saved", "assessment.results_withdrawn"} <= set(actions)
