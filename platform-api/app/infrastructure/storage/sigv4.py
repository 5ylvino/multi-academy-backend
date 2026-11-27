"""AWS Signature Version 4 for S3-compatible storage (Neon Object Storage, AWS S3, R2).

Kept small and dependency-free. Payloads are always hashed (never UNSIGNED-PAYLOAD), so the
storage service verifies the body it receives is the body we signed.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from datetime import datetime
from urllib.parse import quote

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def signing_key(secret: str, day: str, region: str, service: str = "s3") -> bytes:
    k_date = _hmac(("AWS4" + secret).encode(), day)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    return _hmac(k_service, "aws4_request")


def canonical_path(path: str) -> str:
    """S3 encodes each path segment once and keeps the slashes."""
    return quote(path, safe="/-_.~")


def sign(
    *,
    method: str,
    path: str,
    query: Mapping[str, str],
    headers: Mapping[str, str],
    payload_sha256: str,
    access_key: str,
    secret_key: str,
    region: str,
    at: datetime,
    service: str = "s3",
) -> str:
    """Return the Authorization header value. ``headers`` must include host and x-amz-date."""
    amz_date = at.strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    lowered = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
    signed_headers = ";".join(sorted(lowered))
    canonical_headers = "".join(f"{k}:{lowered[k]}\n" for k in sorted(lowered))
    canonical_query = "&".join(
        f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(query.items())
    )
    canonical_request = "\n".join(
        [
            method.upper(),
            canonical_path(path),
            canonical_query,
            canonical_headers,
            signed_headers,
            payload_sha256,
        ]
    )
    scope = f"{day}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    signature = hmac.new(
        signing_key(secret_key, day, region, service), string_to_sign.encode(), hashlib.sha256
    ).hexdigest()
    return (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
