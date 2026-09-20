from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.timetable import SwapSuggestion, TimetableSolveResponse, TimetableSlotInput
from app.security.service_jwt import issue_service_token
from app.services.timetable_solver import solve_with_ortools

client = TestClient(app)


def _headers(features: list[str]) -> dict[str, str]:
    token = issue_service_token(
        tenant_id="tenant-1",
        actor_id="admin-1",
        roles=["principal"],
        features=features,
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tenant-1"}


def test_ortools_resolves_teacher_clash() -> None:
    class_slots = [
        TimetableSlotInput(
            slotId="s1",
            classId="c1",
            teacherId="t1",
            dayOfWeek=1,
            startTime="08:00",
            endTime="09:00",
        ),
        TimetableSlotInput(
            slotId="s2",
            classId="c1",
            teacherId="t2",
            dayOfWeek=1,
            startTime="09:00",
            endTime="10:00",
        ),
    ]
    all_slots = [
        *class_slots,
        TimetableSlotInput(
            slotId="x1",
            classId="c2",
            teacherId="t1",
            dayOfWeek=1,
            startTime="08:00",
            endTime="09:00",
        ),
    ]
    from app.schemas.timetable import TimetableSolveRequest

    result = solve_with_ortools(
        TimetableSolveRequest(classId="c1", classSlots=class_slots, allSlots=all_slots, explain=False)
    )
    assert result.clash_count >= 1
    assert result.solver == "ortools"
    assert result.suggestions
    moved = result.suggestions[0]
    assert moved.proposed_day is not None or "manually" in moved.suggestion.lower()


def test_timetable_route_requires_feature() -> None:
    res = client.post(
        "/v1/timetable/solve",
        headers=_headers([]),
        json={"classId": "c1", "classSlots": [], "allSlots": []},
    )
    assert res.status_code == 403


def test_timetable_route_success() -> None:
    mock = TimetableSolveResponse(
        classId="c1",
        slotCount=2,
        clashCount=1,
        solver="ortools",
        suggestions=[
            SwapSuggestion(
                slotId="s1",
                clashCount=1,
                currentDay=1,
                currentStart="08:00",
                currentEnd="09:00",
                proposedDay=1,
                proposedStart="09:00",
                proposedEnd="10:00",
                suggestion="Move slot",
                explanation="Move Maths to second period.",
            )
        ],
    )
    with patch("app.api.v1.timetable.solve_with_ortools", return_value=mock):
        with patch("app.api.v1.timetable.TimetableExplainChain.enrich", new=AsyncMock(return_value=mock)):
            res = client.post(
                "/v1/timetable/solve",
                headers=_headers(["ai.timetable_solver"]),
                json={
                    "classId": "c1",
                    "classSlots": [
                        {
                            "slotId": "s1",
                            "classId": "c1",
                            "teacherId": "t1",
                            "dayOfWeek": 1,
                            "startTime": "08:00",
                            "endTime": "09:00",
                        }
                    ],
                    "allSlots": [],
                    "explain": False,
                },
            )
    assert res.status_code == 200
    assert res.json()["solver"] == "ortools"
    assert res.json()["suggestions"][0]["slotId"] == "s1"
