"""Activity log (audit trail) for school leaders and owner-console staff."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.presentation.http.dependencies.auth import PlatformPrincipal, SchoolPrincipal
from app.presentation.http.dependencies.context import UseCasesDep
from app.presentation.http.v1.audit.schemas import (
    AuditActionResponse,
    AuditEventResponse,
    AuditPageResponse,
)
from app.presentation.http.v1.schemas import PageMeta
from app.use_cases.audit.catalog import ACTIONS
from app.use_cases.audit.trail import AuditEventView, AuditPage, AuditSearch

school_router = APIRouter(prefix="/audit-events", tags=["activity log"])
platform_router = APIRouter(prefix="/platform/audit-events", tags=["activity log"])

ActionQuery = Annotated[
    str | None,
    Query(
        max_length=120,
        description="An action code or its start: 'finance.' lists every finance event.",
    ),
]
SinceQuery = Annotated[datetime | None, Query(description="From this time (inclusive).")]
UntilQuery = Annotated[datetime | None, Query(description="Up to this time (exclusive).")]
CursorQuery = Annotated[str | None, Query(max_length=128)]
LimitQuery = Annotated[int, Query(ge=1, le=200)]
TextQuery = Annotated[str | None, Query(max_length=120)]

_CSV_COLUMNS = (
    "at",
    "label",
    "action",
    "category",
    "actor_name",
    "actor_email",
    "actor_type",
    "target_type",
    "target_id",
    "school_id",
    "ip_address",
    "device",
    "request_id",
    "details",
)
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _search(
    *,
    actor_id: UUID | None,
    action: str | None,
    target_type: str | None,
    target_id: str | None,
    since: datetime | None,
    until: datetime | None,
    cursor: str | None = None,
    limit: int | None = None,
) -> AuditSearch:
    return AuditSearch(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        since=since,
        until=until,
        cursor=cursor,
        limit=limit,
    )


def _page(page: AuditPage) -> AuditPageResponse:
    return AuditPageResponse(
        items=[AuditEventResponse.model_validate(e) for e in page.events],
        page=PageMeta(next_cursor=page.next_cursor),
    )


def _cell(value: object) -> str:
    """Spreadsheet-safe text: a value that starts like a formula is quoted so Excel won't run it."""
    if value is None:
        return ""
    text = value.isoformat() if isinstance(value, datetime) else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def _csv(events: list[AuditEventView], filename: str) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_CSV_COLUMNS)
    for event in events:
        row = []
        for column in _CSV_COLUMNS:
            value = getattr(event, column)
            if column == "details":
                value = json.dumps(dict(value), sort_keys=True, default=str)
            row.append(_cell(value))
        writer.writerow(row)
    return Response(
        content="﻿" + buffer.getvalue(),  # BOM so Excel reads UTF-8 names correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _actions() -> list[AuditActionResponse]:
    return [
        AuditActionResponse(action=code, label=info.label, category=info.category.value)
        for code, info in sorted(ACTIONS.items())
    ]


# ---- school ------------------------------------------------------------------------------


@school_router.get("", response_model=AuditPageResponse, summary="Search the activity log")
async def school_events(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    actor_id: UUID | None = None,
    action: ActionQuery = None,
    target_type: TextQuery = None,
    target_id: TextQuery = None,
    since: SinceQuery = None,
    until: UntilQuery = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = 50,
) -> AuditPageResponse:
    """Who did what in this school, newest first. Needs `audit_logs:view` (director and IT
    admin by default)."""
    page = await uc.audit_trail.school_events(
        principal,
        _search(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            since=since,
            until=until,
            cursor=cursor,
            limit=limit,
        ),
    )
    return _page(page)


@school_router.get("/export", summary="Download the activity log as CSV")
async def export_school_events(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    since: datetime,
    until: datetime,
    actor_id: UUID | None = None,
    action: ActionQuery = None,
    target_type: TextQuery = None,
    target_id: TextQuery = None,
) -> Response:
    """Up to 10,000 events between two dates. The download itself is recorded in the log."""
    events = await uc.audit_trail.export_school_events(
        principal,
        _search(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            since=since,
            until=until,
        ),
    )
    return _csv(events, f"activity-log-{since.date()}-to-{until.date()}.csv")


@school_router.get(
    "/actions", response_model=list[AuditActionResponse], summary="Every kind of logged event"
)
async def school_actions(principal: SchoolPrincipal) -> list[AuditActionResponse]:
    """Codes and plain-English names, for filters in the activity log screen."""
    principal.require("audit_logs:view")
    return _actions()


# ---- owner console -----------------------------------------------------------------------


@platform_router.get("", response_model=AuditPageResponse, summary="Search all activity")
async def platform_events(
    principal: PlatformPrincipal,
    uc: UseCasesDep,
    school_id: UUID | None = None,
    console_only: Annotated[
        bool, Query(description="Only owner-console activity (no school).")
    ] = False,
    actor_id: UUID | None = None,
    action: ActionQuery = None,
    target_type: TextQuery = None,
    target_id: TextQuery = None,
    since: SinceQuery = None,
    until: UntilQuery = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = 50,
) -> AuditPageResponse:
    """Every school's activity and the console's own. Needs `audit:read`."""
    page = await uc.audit_trail.platform_events(
        principal,
        _search(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            since=since,
            until=until,
            cursor=cursor,
            limit=limit,
        ),
        school_id=school_id,
        console_only=console_only,
    )
    return _page(page)


@platform_router.get("/export", summary="Download activity as CSV")
async def export_platform_events(
    principal: PlatformPrincipal,
    uc: UseCasesDep,
    since: datetime,
    until: datetime,
    school_id: UUID | None = None,
    console_only: bool = False,
    actor_id: UUID | None = None,
    action: ActionQuery = None,
    target_type: TextQuery = None,
    target_id: TextQuery = None,
) -> Response:
    events = await uc.audit_trail.export_platform_events(
        principal,
        _search(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            since=since,
            until=until,
        ),
        school_id=school_id,
        console_only=console_only,
    )
    return _csv(events, f"platform-activity-{since.date()}-to-{until.date()}.csv")


@platform_router.get(
    "/actions", response_model=list[AuditActionResponse], summary="Every kind of logged event"
)
async def platform_actions(principal: PlatformPrincipal) -> list[AuditActionResponse]:
    principal.require("audit:read")
    return _actions()
