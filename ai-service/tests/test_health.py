from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_live_health() -> None:
    res = client.get("/health/live")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
