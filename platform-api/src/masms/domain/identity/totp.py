"""Time-based one-time passwords (RFC 6238, HMAC-SHA1, 6 digits, 30 s steps).

The parameters match what every authenticator app (Google Authenticator, Microsoft
Authenticator, 1Password, Authy) expects by default.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
from datetime import datetime
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
#: Steps of clock drift accepted either side of now.
DRIFT_STEPS = 1


def time_step(at: datetime) -> int:
    return int(at.timestamp()) // STEP_SECONDS


def code_at(secret: bytes, step: int) -> str:
    digest = hmac.new(secret, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def matching_step(secret: bytes, code: str, at: datetime, *, after_step: int | None) -> int | None:
    """The step ``code`` belongs to, if valid now and newer than ``after_step`` (no replays)."""
    if len(code) != DIGITS or not code.isdigit():
        return None
    now = time_step(at)
    for step in range(now - DRIFT_STEPS, now + DRIFT_STEPS + 1):
        if after_step is not None and step <= after_step:
            continue
        if hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def encode_secret(secret: bytes) -> str:
    return base64.b32encode(secret).decode().rstrip("=")


def provisioning_uri(secret: bytes, *, account: str, issuer: str) -> str:
    label = quote(f"{issuer}:{account}")
    query = urlencode(
        {
            "secret": encode_secret(secret),
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": DIGITS,
            "period": STEP_SECONDS,
        }
    )
    return f"otpauth://totp/{label}?{query}"
