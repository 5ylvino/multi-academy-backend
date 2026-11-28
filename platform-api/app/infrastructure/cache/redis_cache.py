"""Redis-backed cache, version counters and rate limiting.

The cache degrades gracefully: if Redis is unreachable, reads miss and writes are skipped, so the
API keeps serving from the database instead of failing. Rate limiting fails open for the same
reason, but logs loudly.
"""

from __future__ import annotations

from typing import Any

import orjson
import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.domain.shared.errors import RateLimited

_log = structlog.get_logger(__name__)
_PREFIX = "masms:"


class RedisCache:
    def __init__(self, client: Redis) -> None:
        self._redis = client

    async def get_json(self, key: str) -> Any | None:
        try:
            raw = await self._redis.get(_PREFIX + key)
        except RedisError:
            _log.warning("cache.get_failed", key=key)
            return None
        return None if raw is None else orjson.loads(raw)

    async def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        try:
            await self._redis.set(_PREFIX + key, orjson.dumps(value), ex=ttl_seconds)
        except RedisError:
            _log.warning("cache.set_failed", key=key)

    async def delete(self, *keys: str) -> None:
        if not keys:
            return
        try:
            await self._redis.delete(*(_PREFIX + k for k in keys))
        except RedisError:
            _log.error("cache.delete_failed", keys=list(keys))

    async def get_version(self, name: str) -> int:
        try:
            raw = await self._redis.get(f"{_PREFIX}v:{name}")
        except RedisError:
            return 0
        return int(raw) if raw is not None else 0

    async def bump_version(self, name: str) -> int:
        try:
            return int(await self._redis.incr(f"{_PREFIX}v:{name}"))
        except RedisError:
            _log.error("cache.bump_failed", name=name)
            return 0


class RedisRateLimiter:
    """Fixed-window counter. One round trip per hit (INCR + EXPIRE NX in a pipeline)."""

    def __init__(self, client: Redis) -> None:
        self._redis = client

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
        full_key = f"{_PREFIX}rl:{key}"
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                pipe.incr(full_key)
                pipe.expire(full_key, window_seconds, nx=True)
                count, _ = await pipe.execute()
        except RedisError:
            _log.error("ratelimit.unavailable", key=key)
            return
        if int(count) > limit:
            raise RateLimited(
                "Too many attempts. Please wait and try again.",
                details={"retry_after_seconds": window_seconds},
            )


def create_redis(url: str) -> Redis:
    client: Redis = Redis.from_url(
        url,
        decode_responses=False,
        socket_timeout=0.5,
        socket_connect_timeout=1.0,
        health_check_interval=30,
        max_connections=200,
    )
    return client
