"""Map domain errors to RFC 9457 problem responses."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.shared.errors import (
    AccountLocked,
    AuthenticationFailed,
    Conflict,
    DomainError,
    FeatureDisabled,
    InvalidStateTransition,
    NotFound,
    PermissionDenied,
    RateLimited,
    TenantUnavailable,
    ValidationFailed,
)

_log = structlog.get_logger(__name__)

_STATUS: tuple[tuple[type[DomainError], int], ...] = (
    (ValidationFailed, 422),
    (NotFound, 404),
    (Conflict, 409),
    (InvalidStateTransition, 409),
    (AuthenticationFailed, 401),
    (AccountLocked, 423),
    (PermissionDenied, 403),
    (TenantUnavailable, 403),
    (FeatureDisabled, 403),
    (RateLimited, 429),
)


def _status_for(exc: DomainError) -> int:
    for kind, status in _STATUS:
        if isinstance(exc, kind):
            return status
    return 400


def problem(
    request: Request, status: int, code: str, title: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://docs.mas.ng/errors/{code}",
        "title": title,
        "status": status,
        "code": code,
        "request_id": getattr(request.state, "request_id", None),
    }
    if details:
        body["details"] = details
    headers: dict[str, str] = {}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if status == 429 and details and "retry_after_seconds" in details:
        headers["Retry-After"] = str(details["retry_after_seconds"])
    return JSONResponse(
        body, status_code=status, media_type="application/problem+json", headers=headers
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        return problem(request, _status_for(exc), exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": ".".join(str(p) for p in err["loc"] if p != "body"), "message": err["msg"]}
            for err in exc.errors()
        ]
        return problem(
            request, 422, "validation_failed", "Some fields are not valid.", {"errors": errors}
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return problem(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        _log.exception("request.unhandled_error", path=request.url.path)
        return problem(
            request, 500, "internal_error", "Something went wrong on our side. Please try again."
        )
