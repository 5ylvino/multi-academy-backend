"""Shared limits for list endpoints."""

from __future__ import annotations

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(limit, MAX_PAGE_SIZE))
