from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from app.models import ProviderConfig
from app.services.provider_health import (
    _ping_ai_gateway,
    _ping_nvidia_nim,
    ping_provider,
)


def test_ping_nvidia_nim_success() -> None:
    response = httpx.Response(
        200,
        json={"object": "list", "data": []},
        request=httpx.Request("GET", "https://integrate.api.nvidia.com/v1/models"),
    )

    with patch("app.services.provider_health.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.get.return_value = response
        result = _ping_nvidia_nim("nvapi-test-key")

    assert result["ok"] is True
    assert result["healthStatus"] == "green"
    assert "NVIDIA NIM" in result["message"]


def test_ping_ai_gateway_routes_nvapi_to_nim() -> None:
    with patch("app.services.provider_health._ping_nvidia_nim") as ping_nim:
        ping_nim.return_value = {"ok": True, "healthStatus": "green", "message": "nim"}
        result = _ping_ai_gateway({"api_key": "nvapi-abc"}, {})

    ping_nim.assert_called_once_with("nvapi-abc", base_url=None)
    assert result["ok"] is True


def test_ping_ai_gateway_routes_jwt_to_kilo() -> None:
    with patch("app.services.provider_health._ping_kilo") as ping_kilo:
        ping_kilo.return_value = {"ok": False, "healthStatus": "red", "message": "kilo"}
        result = _ping_ai_gateway({"api_key": "eyJhbGciOiJIUzI1NiJ9.token"}, {})

    ping_kilo.assert_called_once()
    assert result["message"] == "kilo"


def test_ping_provider_ai_without_secrets_is_ai_service_managed() -> None:
    config = MagicMock(spec=ProviderConfig)
    config.id = 1
    config.provider_id = "nvidia"
    config.capability = "ai"
    config.mode = "live"

    db = MagicMock()
    with patch("app.services.provider_health._active_secrets", return_value={}):
        result = ping_provider(db, config)

    assert result["ok"] is True
    assert result["healthStatus"] == "amber"
    assert result["detail"]["aiServiceManaged"] is True
    assert "AI microservice" in result["message"]
