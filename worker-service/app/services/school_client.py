from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings, get_settings
from app.security.service_jwt import issue_internal_token


class SchoolClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._base = self._settings.school_internal_api_url.rstrip("/")

    async def create_notification(self, tenant_id: str, actor_id: str, payload: dict[str, Any]) -> Any:
        token = issue_internal_token(tenant_id, actor_id, self._settings)
        url = f"{self._base}/internal/worker/notifications"
        headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id}
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(url, json=payload, headers=headers)
            res.raise_for_status()
            body = res.json()
            if isinstance(body, dict) and body.get("has_error") is False:
                return body.get("data")
            return body

    async def download_report(
        self, tenant_id: str, actor_id: str, report_id: str, fmt: str
    ) -> Any:
        token = issue_internal_token(tenant_id, actor_id, self._settings)
        url = f"{self._base}/internal/worker/reports/download"
        headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id}
        async with httpx.AsyncClient(timeout=120.0) as client:
            res = await client.post(
                url,
                json={"reportId": report_id, "format": fmt},
                headers=headers,
            )
            res.raise_for_status()
            body = res.json()
            if isinstance(body, dict) and body.get("has_error") is False:
                return body.get("data")
            return body
