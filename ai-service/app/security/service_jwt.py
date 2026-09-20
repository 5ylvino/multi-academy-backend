from __future__ import annotations

import time
import uuid
from typing import Any

from jose import JWTError, jwt

from app.config import Settings, get_settings
from app.security.context import RequestContext


class ServiceJwtError(Exception):
    pass


def _settings() -> Settings:
    return get_settings()


def issue_service_token(
    *,
    tenant_id: str,
    actor_id: str,
    roles: list[str] | None = None,
    features: list[str] | None = None,
    ttl_seconds: int = 300,
    settings: Settings | None = None,
) -> str:
    """Issue a short-lived m2m token (used by NestJS AiServiceClient and tests)."""
    cfg = settings or _settings()
    now = int(time.time())
    payload = {
        "iss": cfg.service_jwt_issuer,
        "aud": cfg.service_jwt_audience,
        "sub": "service",
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "roles": roles or [],
        "features": features or [],
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, cfg.service_jwt_secret, algorithm="HS256")


def decode_service_token(token: str, settings: Settings | None = None) -> dict[str, Any]:
    cfg = settings or _settings()
    try:
        payload = jwt.decode(
            token,
            cfg.service_jwt_secret,
            algorithms=["HS256"],
            audience=cfg.service_jwt_audience,
            issuer=cfg.service_jwt_issuer,
        )
    except JWTError as exc:
        raise ServiceJwtError("Invalid service token") from exc

    tenant_id = str(payload.get("tenant_id") or "").strip()
    actor_id = str(payload.get("actor_id") or "").strip()
    if not tenant_id or not actor_id:
        raise ServiceJwtError("Token missing tenant_id or actor_id")
    return payload


def build_request_context(
    payload: dict[str, Any],
    *,
    header_tenant_id: str | None = None,
    request_id: str | None = None,
) -> RequestContext:
    tenant_id = str(payload.get("tenant_id") or "").strip()
    if header_tenant_id and header_tenant_id.strip() and header_tenant_id.strip() != tenant_id:
        raise ServiceJwtError("Tenant header does not match token")

    roles_raw = payload.get("roles") or []
    features_raw = payload.get("features") or []
    roles = [str(r).lower() for r in roles_raw] if isinstance(roles_raw, list) else []
    features = [str(f) for f in features_raw] if isinstance(features_raw, list) else []

    return RequestContext(
        tenant_id=tenant_id,
        actor_id=str(payload.get("actor_id") or "").strip(),
        roles=roles,
        features=features,
        request_id=request_id,
    )
