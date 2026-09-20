from unittest.mock import patch


def test_ready_health_returns_503_when_database_is_down(client) -> None:
    with patch("app.api.health.ping_database", return_value=False):
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
