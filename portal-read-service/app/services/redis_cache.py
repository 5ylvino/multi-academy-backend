from __future__ import annotations

import json
from typing import Any

import redis

from app.config import Settings, get_settings


class PortalCache:
    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self._prefix = cfg.portal_cache_prefix
        self._ttl = cfg.portal_cache_ttl_seconds
        self._client: redis.Redis | None = None
        if cfg.redis_url:
            self._client = redis.from_url(cfg.redis_url, decode_responses=True)

    def _key(self, tenant_id: str, path: str, actor_id: str) -> str:
        safe = path.replace("/", ":").strip(":")
        return f"{self._prefix}{tenant_id}:{actor_id}:{safe}"

    async def get(self, tenant_id: str, path: str, actor_id: str) -> Any | None:
        if not self._client:
            return None
        raw = self._client.get(self._key(tenant_id, path, actor_id))
        if not raw:
            return None
        return json.loads(raw)

    async def set(self, tenant_id: str, path: str, actor_id: str, value: Any) -> None:
        if not self._client:
            return
        self._client.setex(
            self._key(tenant_id, path, actor_id),
            self._ttl,
            json.dumps(value, default=str),
        )
