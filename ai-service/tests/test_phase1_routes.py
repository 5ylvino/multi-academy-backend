from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.common import AiResponse, UsageInfo
from app.security.service_jwt import issue_service_token

client = TestClient(app)


@pytest.fixture(autouse=True)
def _noop_audit():
    with patch("app.api.v1.assistant.write_audit_event"), patch(
        "app.api.v1.support.write_audit_event"
    ), patch("app.api.v1.essay.write_audit_event"):
        yield


def _auth_headers(tenant_id: str = "tenant-1", features: list[str] | None = None) -> dict[str, str]:
    resolved_features = ["ai.assistant"] if features is None else features
    token = issue_service_token(
        tenant_id=tenant_id,
        actor_id="user-1",
        roles=["teacher"],
        features=resolved_features,
    )
    return {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id}


def test_assistant_requires_feature() -> None:
    headers = _auth_headers(features=[])
    res = client.post(
        "/v1/assistant/chat",
        headers=headers,
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert res.status_code == 403


def test_assistant_chat_success() -> None:
    mock_response = AiResponse(
        content="Hello",
        providerId="nvidia",
        model="test-model",
        usage=UsageInfo(promptTokens=10, completionTokens=5),
        sources=[],
    )
    with patch("app.api.v1.assistant.AssistantChain.run", new=AsyncMock(return_value=mock_response)):
        res = client.post(
            "/v1/assistant/chat",
            headers=_auth_headers(features=["ai.assistant"]),
            json={"messages": [{"role": "user", "content": "What are the fees?"}]},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["content"] == "Hello"
    assert body["providerId"] == "nvidia"


def test_support_chat_success() -> None:
    mock_response = AiResponse(
        content="Open Parent portal → Fees",
        providerId="nvidia",
        model="test-model",
        usage=UsageInfo(promptTokens=8, completionTokens=4),
        sources=[],
    )
    with patch("app.api.v1.support.SupportChain.run", new=AsyncMock(return_value=mock_response)):
        res = client.post(
            "/v1/support/chat",
            headers=_auth_headers(features=["ai.support_chatbot"]),
            json={"messages": [{"role": "user", "content": "How do I pay fees?"}]},
        )
    assert res.status_code == 200
    assert "Fees" in res.json()["content"]


def test_essay_grade_success() -> None:
    from app.schemas.chat import EssayGradeResponse

    mock_response = EssayGradeResponse(
        content='{"score":82,"feedback":"Good structure"}',
        providerId="nvidia",
        model="test-model",
        score=82,
        feedback="Good structure",
    )
    with patch("app.api.v1.essay.EssayChain.run", new=AsyncMock(return_value=mock_response)):
        res = client.post(
            "/v1/essay/grade",
            headers=_auth_headers(features=["ai.essay_grading"]),
            json={"essayText": "Sample essay about school."},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["score"] == 82
