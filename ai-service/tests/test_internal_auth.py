from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security.service_jwt import issue_service_token

client = TestClient(app)


def test_llm_ping_requires_auth() -> None:
    res = client.post("/v1/internal/llm/ping")
    assert res.status_code == 401


def test_llm_ping_with_valid_token() -> None:
    token = issue_service_token(
        tenant_id="tenant-1",
        actor_id="user-1",
        features=["ai.assistant"],
    )
    with patch("app.api.internal.ping_llm", new=AsyncMock(return_value={"content": "pong", "providerId": "nvidia", "model": "test"})):
        res = client.post(
            "/v1/internal/llm/ping",
            headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": "tenant-1"},
        )
    assert res.status_code == 200
    assert res.json()["content"] == "pong"
