from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings, get_settings
from app.security.context import RequestContext
from app.security.service_jwt import issue_service_token


class SchoolClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._base = self._settings.school_internal_api_url.rstrip("/")

    async def fetch_internal(self, ctx: RequestContext, path: str) -> Any:
        token = issue_service_token(
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
            roles=ctx.roles,
            features=ctx.features,
            audience="mas-school-internal",
            settings=self._settings,
        )
        url = f"{self._base}/internal/portal/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": ctx.tenant_id,
        }
        if ctx.request_id:
            headers["X-Request-Id"] = ctx.request_id
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.get(url, headers=headers)
            res.raise_for_status()
            body = res.json()
            if isinstance(body, dict) and body.get("has_error") is False:
                return body.get("data")
            return body
