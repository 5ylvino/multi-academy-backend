from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.config import Settings, get_settings


class ControlClient:
    """M2M client for control-plane runtime config and provider secrets."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        self._secrets_cache: dict[str, tuple[float, dict[str, Any]]] = {}
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

    async def _authed_get(self, path: str) -> dict[str, Any]:
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.get(
                f"{self._base_url()}{path}",
                headers={"Authorization": f"Bearer {token}"},
            )
        if res.status_code == 401 and self._token:
            self._token = None
            token = await self._access_token()
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.get(
                    f"{self._base_url()}{path}",
                    headers={"Authorization": f"Bearer {token}"},
                )
        res.raise_for_status()
        return res.json()

    async def get_provider_secrets(self, tenant_ref: str, capability: str = "payment") -> dict[str, Any]:
        cache_key = f"{tenant_ref}:{capability}"
        ttl = self.settings.control_secrets_ttl_seconds
        cached = self._secrets_cache.get(cache_key)
        if cached and time.time() < cached[0]:
            return cached[1]
        path = (
            f"/internal/v1/provider-secrets/{capability}"
            if tenant_ref == "__platform__"
            else f"/internal/v1/tenants/{tenant_ref}/provider-secrets/{capability}"
        )
        payload = await self._authed_get(path)
        self._secrets_cache[cache_key] = (time.time() + ttl, payload)
        return payload

    async def get_runtime_config(self, tenant_ref: str) -> dict[str, Any]:
        ttl = self.settings.control_secrets_ttl_seconds
        cached = self._runtime_cache.get(tenant_ref)
        if cached and time.time() < cached[0]:
            return cached[1]
        payload = await self._authed_get(f"/internal/v1/tenants/{tenant_ref}/runtime-config")
        self._runtime_cache[tenant_ref] = (time.time() + ttl, payload)
        return payload

    async def get_runtime_config_safe(self, tenant_ref: str) -> dict[str, Any]:
        try:
            return await self.get_runtime_config(tenant_ref)
        except Exception:
            return {}

    async def default_gateway_id(self, tenant_ref: str) -> str:
        if not self.is_configured():
            return "paystack"
        config = await self.get_runtime_config(tenant_ref)
        provider_id = (
            (config.get("providers") or {})
            .get("payment", {})
            .get("providerId")
        )
        return str(provider_id or "disabled")


control_client = ControlClient()
