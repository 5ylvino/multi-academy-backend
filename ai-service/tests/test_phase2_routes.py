from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.guidance import CoachingResource, ParentCoachingResponse, StudyPlanResponse
from app.schemas.tutor import TutorChatResponse, TutorQuiz, TutorQuizOption, TopicMasteryItem, TopicMasteryResponse
from app.security.service_jwt import issue_service_token

client = TestClient(app)


@pytest.fixture(autouse=True)
def _noop_audit():
    with patch("app.api.v1.tutor.write_audit_event"), patch(
        "app.api.v1.guidance.write_audit_event"
    ):
        yield


def _auth_headers(tenant_id: str = "tenant-1", features: list[str] | None = None) -> dict[str, str]:
    resolved = features or []
    token = issue_service_token(
        tenant_id=tenant_id,
        actor_id="student-1",
        roles=["student"],
        features=resolved,
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id}


def test_tutor_requires_feature() -> None:
    res = client.post(
        "/v1/tutor/chat",
        headers=_auth_headers(features=[]),
        json={"message": "Explain fractions", "topic": "Fractions"},
    )
    assert res.status_code == 403


def test_tutor_chat_success() -> None:
    mock = TutorChatResponse(
        sessionId="sess-1",
        content="Fractions represent parts of a whole.",
        state="quiz",
        masteryPct=0,
        quiz=TutorQuiz(
            question="What is 1/2 of 10?",
            options=[TutorQuizOption(label="A", value="a"), TutorQuizOption(label="B", value="b")],
            correctValue="a",
        ),
        providerId="nvidia",
        model="test-model",
        disclaimer="AI tutor support",
    )
    with patch("app.api.v1.tutor.TutorGraph.run_turn", new=AsyncMock(return_value=mock)):
        res = client.post(
            "/v1/tutor/chat",
            headers=_auth_headers(features=["ai.tutor"]),
            json={"message": "Explain fractions", "topic": "Fractions"},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["content"]
    assert body["quiz"]["question"]


def test_guidance_home_success() -> None:
    mock = ParentCoachingResponse(
        studentId="student-1",
        topic="Fractions",
        subject="Mathematics",
        resources=[
            CoachingResource(type="video", title="Fractions intro", url="https://example.com", vetted=True),
        ],
        tonightChecklist=["Review homework question 3"],
        atHome=["Ask your child to explain one fraction problem"],
        sources=["faq:1"],
        disclaimer="Study support only",
    )
    with patch("app.api.v1.guidance.ParentCoachAgent.run", new=AsyncMock(return_value=mock)):
        res = client.post(
            "/v1/guidance/home",
            headers=_auth_headers(features=["ai.performance_recommendations"]),
            json={"studentId": "student-1"},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["topic"] == "Fractions"
    assert body["resources"]


def test_study_plan_success() -> None:
    mock = StudyPlanResponse(
        studentId="student-1",
        actions=["Revise fractions for 15 minutes"],
        focusTopics=["Fractions"],
        masterySummary=[{"topicKey": "fractions", "masteryPct": 40, "note": "Weak area"}],
        sources=["ai_topic_mastery"],
        sourceMode="ai",
        disclaimer="AI plan",
    )
    with patch("app.api.v1.guidance.StudyPlanChain.run", new=AsyncMock(return_value=mock)):
        res = client.post(
            "/v1/guidance/study-plan",
            headers=_auth_headers(features=["ai.performance_recommendations"]),
            json={},
        )
    assert res.status_code == 200
    assert res.json()["actions"]


def test_tutor_mastery_success() -> None:
    mock_items = [
        TopicMasteryItem(topicKey="math:fractions", masteryPct=45, attemptCount=2, correctCount=1),
    ]
    with patch("app.api.v1.tutor.MasteryService.list_mastery", return_value=mock_items):
        res = client.get(
            "/v1/tutor/mastery?studentId=student-1",
            headers=_auth_headers(features=["ai.tutor"]),
        )
    assert res.status_code == 200
    body = res.json()
    assert body["items"][0]["masteryPct"] == 45
