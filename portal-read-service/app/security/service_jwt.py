from __future__ import annotations

import time
import uuid
from typing import Any

from jose import JWTError, jwt

from app.config import Settings, get_settings
from app.security.context import RequestContext


class ServiceJwtError(Exception):
    pass


def issue_service_token(
    *,
    tenant_id: str,
    actor_id: str,
    roles: list[str] | None = None,
    features: list[str] | None = None,
    ttl_seconds: int = 300,
    settings: Settings | None = None,
    audience: str | None = None,
) -> str:
    cfg = settings or get_settings()
    now = int(time.time())
    payload = {
        "iss": cfg.service_jwt_issuer,
        "aud": audience or "mas-school-internal",
        "sub": "service",
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "roles": roles or [],
        "features": features or [],
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(
        payload,
        cfg.school_internal_service_jwt_secret or cfg.service_jwt_secret,
        algorithm="HS256",
    )


def decode_service_token(token: str, settings: Settings | None = None) -> dict[str, Any]:
    cfg = settings or get_settings()
    try:
        return jwt.decode(
            token,
            cfg.service_jwt_secret,
            algorithms=["HS256"],
            audience=cfg.service_jwt_audience,
            issuer=cfg.service_jwt_issuer,
        )
    except JWTError as exc:
        raise ServiceJwtError("Invalid service token") from exc


def build_request_context(
    payload: dict[str, Any],
    *,
    header_tenant_id: str | None = None,
    request_id: str | None = None,
) -> RequestContext:
    tenant_id = str(payload.get("tenant_id") or "").strip()
    if header_tenant_id and header_tenant_id.strip() != tenant_id:
        raise ServiceJwtError("Tenant header mismatch")
    actor_id = str(payload.get("actor_id") or "").strip()
    if not tenant_id or not actor_id:
        raise ServiceJwtError("Missing tenant_id or actor_id")
    roles = [str(r).lower() for r in (payload.get("roles") or [])]
    features = [str(f) for f in (payload.get("features") or [])]
    return RequestContext(
        tenant_id=tenant_id,
        actor_id=actor_id,
        roles=roles,
        features=features,
        request_id=request_id,
    )
