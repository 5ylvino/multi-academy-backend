from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.config import Settings, get_settings

DEFAULT_TUTORING_POLICY = {
    "platformFeePercent": 15.0,
    "defaultCurrency": "NGN",
    "allowExternalTutors": True,
    "marketplaceEnabled": True,
}


class ControlClient:
    """M2M client for control-plane runtime config (tutoring policy)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._runtime_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def is_configured(self) -> bool:
        return bool(
            self.settings.control_api_url
            and self.settings.control_m2m_client_id
            and self.settings.control_m2m_client_secret
        )

    def _base_url(self) -> str:
        return self.settings.control_api_url.rstrip("/")

    async def _fetch_token(self) -> str:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                f"{self._base_url()}/internal/v1/token",
                json={
                    "client_id": self.settings.control_m2m_client_id,
                    "client_secret": self.settings.control_m2m_client_secret,
                },
            )
        res.raise_for_status()
        data = res.json()
        lifetime = int(data.get("expires_in") or 900)
        self._token = str(data["access_token"])
        self._token_expires_at = time.time() + max(lifetime - 30, 60)
        return self._token

    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        async with self._token_lock:
            if self._token and time.time() < self._token_expires_at:
                return self._token
            return await self._fetch_token()

    async def get_runtime_config(self, tenant_ref: str) -> dict[str, Any]:
        if not self.is_configured():
            return {}
        ttl = self.settings.control_secrets_ttl_seconds
        cached = self._runtime_cache.get(tenant_ref)
        if cached and time.time() < cached[0]:
            return cached[1]
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.get(
                f"{self._base_url()}/internal/v1/tenants/{tenant_ref}/runtime-config",
                headers={"Authorization": f"Bearer {token}"},
            )
        res.raise_for_status()
        payload = res.json()
        self._runtime_cache[tenant_ref] = (time.time() + ttl, payload)
        return payload

    async def get_tutoring_policy(self, tenant_ref: str) -> dict[str, Any]:
        config = await self.get_runtime_config(tenant_ref)
        policy = config.get("tutoring") or {}
        return {**DEFAULT_TUTORING_POLICY, **policy}


control_client = ControlClient()
