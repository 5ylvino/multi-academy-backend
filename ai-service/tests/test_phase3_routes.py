from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.copilot import CopilotProposal, InterventionPlanBody, InterventionPlanResponse
from app.schemas.insights import ClassInsightsResponse, InsightFlag, TopicHeatmapCell
from app.security.service_jwt import issue_service_token

client = TestClient(app)


@pytest.fixture(autouse=True)
def _noop_audit():
    with patch("app.api.v1.insights.write_audit_event"), patch(
        "app.api.v1.copilot.write_audit_event"
    ):
        yield


def _auth_headers(features: list[str], roles: list[str] | None = None) -> dict[str, str]:
    token = issue_service_token(
        tenant_id="tenant-1",
        actor_id="teacher-1",
        roles=roles or ["teacher"],
        features=features,
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tenant-1"}


def test_insights_detect_requires_feature() -> None:
    res = client.post(
        "/v1/insights/detect",
        headers=_auth_headers([]),
        json={"classId": "class-1", "studentMastery": []},
    )
    assert res.status_code == 403


def test_insights_detect_success() -> None:
    mock = ClassInsightsResponse(
        classId="class-1",
        heatmap=[
            TopicHeatmapCell(
                topicKey="math:circle theorems",
                studentCount=10,
                belowThresholdCount=9,
                avgMasteryPct=42,
                severity="high",
            )
        ],
        flags=[
            InsightFlag(
                topic="circle theorems",
                message="9 students below mastery — circle theorems",
                studentCount=10,
                belowThresholdCount=9,
                severity="high",
            )
        ],
        narration="Focus on circle theorems this week.",
        disclaimer="Advisory only",
    )
    with patch("app.api.v1.insights.InsightsService.detect_class", new=AsyncMock(return_value=mock)):
        res = client.post(
            "/v1/insights/detect",
            headers=_auth_headers(["ai.performance_detection"]),
            json={
                "classId": "class-1",
                "studentMastery": [
                    {"studentId": "s1", "topicKey": "math:circle theorems", "masteryPct": 40}
                ],
            },
        )
    assert res.status_code == 200
    body = res.json()
    assert body["flags"][0]["belowThresholdCount"] == 9


def test_copilot_intervention_success() -> None:
    mock = InterventionPlanResponse(
        planId="plan-1",
        status="draft",
        classId="class-1",
        topic="Circle theorems",
        plan=InterventionPlanBody(
            reteachSuggestion="Use visual proofs",
            questionCount=5,
            smallGroupRecommendation="Group lowest 6 students",
            parentCoachingNote="15-min worksheet at home",
            proposals=[CopilotProposal(type="reteach", title="Re-teach circle theorems")],
        ),
        disclaimer="Draft only",
    )
    with patch(
        "app.api.v1.copilot.TeacherCopilotAgent.create_plan",
        new=AsyncMock(return_value=mock),
    ):
        res = client.post(
            "/v1/copilot/intervention",
            headers=_auth_headers(["ai.teacher_copilot"]),
            json={"classId": "class-1", "topic": "Circle theorems"},
        )
    assert res.status_code == 200
    assert res.json()["plan"]["reteachSuggestion"]


def test_mastery_rollup_success() -> None:
    from app.schemas.insights import MasteryRollupResponse, TopicHeatmapCell

    mock = MasteryRollupResponse(
        classId="class-1",
        topicsProcessed=1,
        rollups=[
            TopicHeatmapCell(
                topicKey="fractions",
                studentCount=2,
                belowThresholdCount=1,
                avgMasteryPct=55,
                severity="medium",
            )
        ],
    )
    with patch("app.api.v1.insights.InsightsService.run_rollup", return_value=mock):
        res = client.post(
            "/v1/insights/mastery-rollup",
            headers=_auth_headers(["ai.performance_detection"]),
            json={
                "classId": "class-1",
                "studentMastery": [
                    {"studentId": "s1", "topicKey": "fractions", "masteryPct": 30},
                    {"studentId": "s2", "topicKey": "fractions", "masteryPct": 80},
                ],
            },
        )
    assert res.status_code == 200
    body = res.json()
    assert body["topicsProcessed"] == 1
    assert body["rollups"][0]["topicKey"] == "fractions"
