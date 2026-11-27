"""One pure-ASGI middleware for request ids, access logs, metrics and security headers.

Pure ASGI (not BaseHTTPMiddleware) keeps per-request overhead low and streams responses untouched.
"""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.infrastructure.observability import metrics

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
                # API responses carry tokens and personal data: never cache them in shared caches.
                if not any(name.lower() == b"cache-control" for name, _ in headers):
                    headers.append((b"cache-control", b"no-store"))
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


class BodySizeLimitMiddleware:
    """Refuse request bodies over a limit before anything buffers or spools them.

    Checks Content-Length up front and also counts streamed chunks, so chunked uploads can't
    slip past. File uploads allow a little over the 5 MB file limit for the form fields.
    """

    def __init__(self, app: ASGIApp, *, default_limit: int, upload_limit: int) -> None:
        self.app = app
        self.default_limit = default_limit
        self.upload_limit = upload_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        is_upload = headers.get(b"content-type", b"").startswith(b"multipart/form-data")
        limit = self.upload_limit if is_upload else self.default_limit
        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _too_large(send)
            return
        received = 0

        async def limited() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge
            return message

        started = False

        async def tracked(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited, tracked)
        except _BodyTooLarge:
            if not started:
                await _too_large(send)


class _BodyTooLarge(Exception):
    pass


async def _too_large(send: Send) -> None:
    body = (
        b'{"type":"https://docs.mas.ng/errors/payload_too_large","title":"Request is too large.",'
        b'"status":413,"code":"payload_too_large"}'
    )
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
