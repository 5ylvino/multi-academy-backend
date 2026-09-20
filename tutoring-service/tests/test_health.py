from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_live_health() -> None:
    res = client.get("/health/live")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_ready_health_returns_503_when_database_is_down() -> None:
    with patch("app.api.health.ping_database", return_value=False):
        res = client.get("/health/ready")

    assert res.status_code == 503
    assert res.json()["status"] == "degraded"
