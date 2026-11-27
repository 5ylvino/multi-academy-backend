"""One pure-ASGI middleware for request ids, access logs, metrics and security headers.

Pure ASGI (not BaseHTTPMiddleware) keeps per-request overhead low and streams responses untouched.
"""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from masms.infrastructure.observability import metrics

_log = structlog.get_logger("masms.access")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"strict-transport-security", b"max-age=63072000; includeSubDomains; preload"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
)
_SKIP_LOG = frozenset({"/health/live", "/health/ready", "/metrics"})


def _route_template(scope: Scope) -> str:
    """Low-cardinality route label, e.g. ``/api/v1/platform/tenants/{tenant_id}``.

    Routes inside included routers report their template without the router prefixes, so the
    static prefix is rebuilt from the leading segments of the real path.
    """
    route = scope.get("route")
    template: str | None = getattr(route, "path", None)
    if not template:
        return "unmatched"
    path: str = scope["path"]
    path_parts = path.rstrip("/").split("/")
    template_parts = template.rstrip("/").split("/")
    keep = len(path_parts) - len(template_parts) + 1  # both lists start with "" for the root
    return "/".join(path_parts[:keep]) + template if keep > 0 else template


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, metrics_enabled: bool = True) -> None:
        self.app = app
        self.metrics_enabled = metrics_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                headers.extend(_SECURITY_HEADERS)
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            template = _route_template(scope)
            if self.metrics_enabled:
                metrics.observe(scope["method"], template, status_code, elapsed)
            if scope["path"] not in _SKIP_LOG:
                _log.info(
                    "request",
                    method=scope["method"],
                    route=template,
                    status=status_code,
                    duration_ms=round(elapsed * 1000, 2),
                )
