"""Identifier generation.

UUIDv7 ids are time-ordered, so new rows land at the right edge of B-tree indexes instead of
splitting random pages. That keeps inserts fast on very large tables.
"""

from __future__ import annotations

import secrets
import time
from uuid import UUID

_RAND_A_BITS = 12
_RAND_B_BITS = 62


def new_id() -> UUID:
    """Return a new RFC 9562 UUID version 7."""
    unix_ms = time.time_ns() // 1_000_000
    rand_a = secrets.randbits(_RAND_A_BITS)
    rand_b = secrets.randbits(_RAND_B_BITS)
    value = (unix_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return UUID(int=value)
