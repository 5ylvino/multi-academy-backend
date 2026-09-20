"""Rate limits with separate staff / m2m / auth budgets.

Backed by Redis when REDIS_URL is set so the limit holds across replicas;
otherwise an in-memory sliding window that is per-process by definition.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque

from app.config import get_settings

logger = logging.getLogger("control.ratelimit")

_lock = threading.Lock()
_buckets: dict[str, deque[float]] = defaultdict(deque)
_last_sweep = 0.0

# The bucket key contains a caller-influenced IP, so without eviction the dict
# grows until the process runs out of memory.
_SWEEP_INTERVAL_SECONDS = 120.0
_MAX_BUCKETS = 50_000

_redis_client = None
_redis_checked = False


def _redis():
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client
    _redis_checked = True
    url = get_settings().redis_url
    if not url:
        return None
    try:
        import redis

        client = redis.Redis.from_url(url, socket_timeout=0.25, socket_connect_timeout=0.25)
        client.ping()
        _redis_client = client
        logger.info("Rate limiter using Redis at %s", url.split("@")[-1])
    except Exception as exc:
        logger.warning("Redis unavailable for rate limiting (%s) — using in-memory buckets", exc)
        _redis_client = None
    return _redis_client


def _prune(window: deque[float], now: float, window_seconds: float) -> None:
    cutoff = now - window_seconds
    while window and window[0] < cutoff:
        window.popleft()


def _sweep(now: float, window_seconds: float) -> None:
    """Drop buckets with no recent activity. Called under the lock."""
    global _last_sweep
    if now - _last_sweep < _SWEEP_INTERVAL_SECONDS and len(_buckets) < _MAX_BUCKETS:
        return
    _last_sweep = now
    cutoff = now - window_seconds
    for key in [k for k, w in _buckets.items() if not w or w[-1] < cutoff]:
        del _buckets[key]


def _check_redis(client, bucket_key: str, limit: int, window_seconds: float) -> tuple[bool, int]:
    now = time.time()
    redis_key = f"rl:{bucket_key}"
    try:
        pipe = client.pipeline()
        pipe.zremrangebyscore(redis_key, 0, now - window_seconds)
        pipe.zadd(redis_key, {f"{now}:{id(pipe)}": now})
        pipe.zcard(redis_key)
        pipe.expire(redis_key, int(window_seconds) + 1)
        count = int(pipe.execute()[2])
    except Exception as exc:
        logger.warning("Redis rate-limit check failed (%s) — allowing request", exc)
        return True, limit
    if count > limit:
        return False, 0
    return True, max(0, limit - count)


def check_rate_limit(bucket_key: str, limit: int, window_seconds: float = 60.0) -> tuple[bool, int]:
    """Return (allowed, remaining) for a sliding window."""
    if limit <= 0:
        return True, 0

    client = _redis()
    if client is not None:
        return _check_redis(client, bucket_key, limit, window_seconds)

    now = time.monotonic()
    with _lock:
        _sweep(now, window_seconds)
        window = _buckets[bucket_key]
        _prune(window, now, window_seconds)
        if len(window) >= limit:
            return False, 0
        window.append(now)
        return True, max(0, limit - len(window))


def reset_buckets() -> None:
    """Test helper — clears in-memory state."""
    with _lock:
        _buckets.clear()


def budget_for_path(path: str) -> tuple[str, int]:
    """Return (budget_name, limit_per_minute) for the request path."""
    settings = get_settings()
    if path.startswith("/internal/v1/token"):
        # Credential-checking endpoint: tighter than the general m2m budget so a
        # leaked client_id cannot be brute-forced at 300 attempts a minute.
        return "m2m_token", settings.rate_limit_auth_per_minute
    if path.startswith("/internal/"):
        return "m2m", settings.rate_limit_m2m_per_minute
    if path.startswith("/v1/auth/login") or path.startswith("/v1/auth/refresh"):
        return "auth", settings.rate_limit_auth_per_minute
    return "staff", settings.rate_limit_staff_per_minute
