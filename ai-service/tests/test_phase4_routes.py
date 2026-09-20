from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.readiness import JambReadinessResponse, SubjectReadiness
from app.schemas.reports import ReportCommentResponse
from app.security.service_jwt import issue_service_token

client = TestClient(app)


def _headers(features: list[str]) -> dict[str, str]:
    token = issue_service_token(
        tenant_id="tenant-1",
        actor_id="teacher-1",
        roles=["teacher"],
        features=features,
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": "tenant-1"}


def test_report_comment_requires_feature() -> None:
    res = client.post(
        "/v1/reports/comment-draft",
        headers=_headers([]),
        json={"studentId": "s1"},
    )
    assert res.status_code == 403


def test_report_comment_success() -> None:
    mock = ReportCommentResponse(
        draftId="d1",
        studentId="s1",
        status="draft",
        commentText="Adaeze shows steady progress in Mathematics.",
        locale="en",
        disclaimer="Draft only",
    )
    with patch("app.api.v1.reports.ReportCommentChain.run", new=AsyncMock(return_value=mock)):
        res = client.post(
            "/v1/reports/comment-draft",
            headers=_headers(["ai.report_comments"]),
            json={"studentId": "s1", "studentName": "Adaeze"},
        )
    assert res.status_code == 200
    assert "progress" in res.json()["commentText"]


def test_jamb_readiness_success() -> None:
    mock = JambReadinessResponse(
        studentId="s1",
        subjects=[
            SubjectReadiness(subjectName="Mathematics", readinessPct=72, attemptCount=5),
        ],
        summary="Focus on timed mocks.",
        disclaimer="Advisory",
    )
    with patch("app.api.v1.readiness.JambReadinessChain.run", new=AsyncMock(return_value=mock)):
        res = client.get(
            "/v1/readiness/jamb?studentId=s1",
            headers=_headers(["academic.jamb_cbt"]),
        )
    assert res.status_code == 200
    assert res.json()["subjects"][0]["readinessPct"] == 72
