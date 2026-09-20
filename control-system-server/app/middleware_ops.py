"""Request ID, IP allowlist and rate-limit middleware for the control API."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.database import SessionLocal
from app.models.security_ops import IpAllowlistEntry, SecuritySettings
from app.services.rate_limit import budget_for_path, check_rate_limit

logger = logging.getLogger("control.middleware")

SKIP_PREFIXES = (
    "/health/",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/public/",
)

# Policy was read from the database on every single request, adding two queries
# to each call. A few seconds of staleness on a security toggle is an acceptable
# trade, and invalidate_policy_cache() makes deliberate changes take effect now.
_POLICY_TTL_SECONDS = 5.0
_policy_cache: dict[str, object] = {"at": 0.0, "enforce": None, "cidrs": []}


def client_ip(request: Request, trusted_proxy_count: int) -> str:
    """Resolve the caller's IP.

    X-Forwarded-For is only consulted when the deployment declares how many
    proxies sit in front of the app. Trusting the left-most entry unconditionally
    lets any caller spoof both the allowlist check and their rate-limit bucket.
    """
    peer = request.client.host if request.client else ""
    if trusted_proxy_count <= 0:
        return peer
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    if not hops:
        return peer
    # Right-most hops are appended by our own proxies; step back past them.
    return hops[max(0, len(hops) - trusted_proxy_count)]


def _ip_allowed(ip: str, cidrs: list[str]) -> bool:
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for cidr in cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if addr in network:
            return True
    return False


def _peek_policy() -> tuple[bool, list[str]] | None:
    now = time.monotonic()
    cached_enforce = _policy_cache["enforce"]
    if cached_enforce is not None and now - float(_policy_cache["at"]) < _POLICY_TTL_SECONDS:  # type: ignore[arg-type]
        return bool(cached_enforce), list(_policy_cache["cidrs"])  # type: ignore[arg-type]
    return None


def _load_policy(env_enforced: bool) -> tuple[bool, list[str]]:
    cached = _peek_policy()
    if cached is not None:
        return cached

    enforce = env_enforced
    cidrs: list[str] = []
    db = SessionLocal()
    try:
        row = db.get(SecuritySettings, 1)
        if row is not None:
            enforce = row.ip_allowlist_enforced
        if enforce:
            entries = db.execute(
                select(IpAllowlistEntry).where(IpAllowlistEntry.is_active.is_(True))
            ).scalars().all()
            cidrs = [e.cidr for e in entries]
    except SQLAlchemyError as exc:
        # An unreachable database must not turn every request into a 500 from
        # inside the middleware. Fall back to the environment setting: if it says
        # enforce, we keep enforcing with an empty list (deny) rather than
        # silently opening the console up.
        logger.error("Could not load security policy (%s) — falling back to env config", exc)
        return env_enforced, []
    finally:
        db.close()

    _policy_cache.update({"at": time.monotonic(), "enforce": enforce, "cidrs": cidrs})
    return enforce, cidrs


def invalidate_policy_cache() -> None:
    _policy_cache.update({"at": 0.0, "enforce": None, "cidrs": []})


class ControlOpsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id

        if any(path.startswith(p) for p in SKIP_PREFIXES):
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response

        settings = get_settings()
        ip = client_ip(request, settings.trusted_proxy_count)
        request.state.client_ip = ip

        budget, limit = budget_for_path(path)
        allowed, remaining = check_rate_limit(f"{budget}:{ip}", limit)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Rate limit exceeded ({budget} budget)",
                    "budget": budget,
                },
                headers={
                    "Retry-After": "60",
                    "X-RateLimit-Budget": budget,
                    "X-Request-ID": request_id,
                },
            )

        cached_policy = _peek_policy()
        if cached_policy is not None:
            enforce, cidrs = cached_policy
        else:
            enforce, cidrs = await asyncio.to_thread(_load_policy, settings.ip_allowlist_enforced)
        if enforce and not path.startswith("/internal/"):
            # Fails closed. An empty allowlist with enforcement on previously
            # skipped the check, so deleting the last entry silently disabled it.
            if not _ip_allowed(ip, cidrs):
                detail = (
                    "IP allowlist is enforced but no active entries exist. Add an "
                    "entry directly in the database or set IP_ALLOWLIST_ENFORCED=false."
                    if not cidrs
                    else "IP not on control allowlist"
                )
                return JSONResponse(
                    status_code=403,
                    content={"detail": detail, "clientIp": ip},
                    headers={"X-Request-ID": request_id},
                )

        response = await call_next(request)
        response.headers["X-RateLimit-Budget"] = budget
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-Request-ID"] = request_id
        return response
