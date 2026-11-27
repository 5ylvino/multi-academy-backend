"""Search and export the audit trail: who did what, when, from where.

School leaders (``audit_logs:view``) see their own school's events. Owner-console staff
(``audit:read``) see every school's events and the console's own. Downloading the trail is itself
recorded, so nobody can quietly pull a copy.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from app.domain.identity.roles import Realm
from app.domain.shared.errors import ValidationFailed
from app.use_cases.audit.catalog import describe
from app.use_cases.shared import audit
from app.use_cases.shared.pagination import clamp_limit
from app.use_cases.shared.ports import (
    AuditFilter,
    AuditRecord,
    Clock,
    Scope,
    UnitOfWorkFactory,
)
from app.use_cases.shared.principal import Principal

#: Largest download in one go. Narrow the dates for more.
EXPORT_LIMIT = 10_000
#: Longest window one search may cover.
MAX_WINDOW = timedelta(days=366)


@dataclass(frozen=True, slots=True)
class AuditSearch:
    actor_id: UUID | None = None
    action: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    cursor: str | None = None
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class AuditEventView:
    id: UUID
    at: datetime
    action: str
    label: str
    category: str
    school_id: UUID | None
    actor_id: UUID | None
    actor_type: str | None
    actor_name: str | None
    actor_email: str | None
    target_type: str
    target_id: str
    details: Mapping[str, Any]
    request_id: str | None
    ip_address: str | None
    device: str | None

    @classmethod
    def of(cls, record: AuditRecord) -> AuditEventView:
        info = describe(record.action)
        return cls(
            id=record.id,
            at=record.created_at,
            action=record.action,
            label=info.label,
            category=info.category.value,
            school_id=record.tenant_id,
            actor_id=record.actor_id,
            actor_type=record.actor_realm,
            actor_name=record.actor_name,
            actor_email=record.actor_email,
            target_type=record.target_type,
            target_id=record.target_id,
            details=record.metadata,
            request_id=record.request_id,
            ip_address=record.ip_address,
            device=record.user_agent,
        )


@dataclass(frozen=True, slots=True)
class AuditPage:
    events: list[AuditEventView] = field(default_factory=list)
    next_cursor: str | None = None


def encode_cursor(at: datetime, event_id: UUID) -> str:
    raw = f"{at.isoformat()}|{event_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        at, event_id = base64.urlsafe_b64decode(padded).decode().split("|", 1)
        parsed = datetime.fromisoformat(at)
        if parsed.tzinfo is None:
            raise ValueError("naive")
        return parsed, UUID(event_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValidationFailed(
            "This page link has expired. Start the search again.", details={"field": "cursor"}
        ) from exc


def _filter(
    search: AuditSearch,
    *,
    tenant_id: UUID | None,
    platform_only: bool,
    limit: int,
) -> AuditFilter:
    if search.since and search.until:
        if search.since >= search.until:
            raise ValidationFailed(
                "The start date must be before the end date.", details={"field": "since"}
            )
        if search.until - search.since > MAX_WINDOW:
            raise ValidationFailed("Search at most one year at a time.", details={"field": "since"})
    for name, value in (("since", search.since), ("until", search.until)):
        if value is not None and value.tzinfo is None:
            raise ValidationFailed(
                "Include a time zone in the date, for example 2026-09-01T00:00:00Z.",
                details={"field": name},
            )
    action = (search.action or "").strip().lower() or None
    if action is not None and (
        len(action) > 120 or not action.replace("_", "").replace(".", "").isalnum()
    ):
        raise ValidationFailed("Unknown action.", details={"field": "action"})
    return AuditFilter(
        tenant_id=tenant_id,
        platform_only=platform_only,
        actor_id=search.actor_id,
        action_prefix=action,
        target_type=(search.target_type or "").strip() or None,
        target_id=(search.target_id or "").strip() or None,
        since=search.since,
        until=search.until,
        before=decode_cursor(search.cursor) if search.cursor else None,
        limit=limit,
    )


class AuditTrail:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    # ---- school leaders ------------------------------------------------------------------

    async def school_events(self, actor: Principal, search: AuditSearch) -> AuditPage:
        actor.require_realm(Realm.SCHOOL)
        actor.require("audit_logs:view")
        tenant_id = actor.school_tenant_id
        return await self._page(
            Scope.tenant(tenant_id), search, tenant_id=tenant_id, platform_only=False
        )

    async def export_school_events(
        self, actor: Principal, search: AuditSearch
    ) -> list[AuditEventView]:
        actor.require_realm(Realm.SCHOOL)
        actor.require("audit_logs:view")
        tenant_id = actor.school_tenant_id
        return await self._export(
            actor, Scope.tenant(tenant_id), search, tenant_id=tenant_id, platform_only=False
        )

    # ---- owner console -------------------------------------------------------------------

    async def platform_events(
        self,
        actor: Principal,
        search: AuditSearch,
        *,
        school_id: UUID | None,
        console_only: bool,
    ) -> AuditPage:
        actor.require_realm(Realm.PLATFORM)
        actor.require("audit:read")
        return await self._page(
            Scope.platform_wide(), search, tenant_id=school_id, platform_only=console_only
        )

    async def export_platform_events(
        self,
        actor: Principal,
        search: AuditSearch,
        *,
        school_id: UUID | None,
        console_only: bool,
    ) -> list[AuditEventView]:
        actor.require_realm(Realm.PLATFORM)
        actor.require("audit:read")
        return await self._export(
            actor, Scope.platform_wide(), search, tenant_id=school_id, platform_only=console_only
        )

    # ---- shared --------------------------------------------------------------------------

    async def _page(
        self, scope: Scope, search: AuditSearch, *, tenant_id: UUID | None, platform_only: bool
    ) -> AuditPage:
        limit = clamp_limit(search.limit)
        query = _filter(search, tenant_id=tenant_id, platform_only=platform_only, limit=limit + 1)
        async with self._uow_factory(scope, read_only=True) as uow:
            records = await uow.audit_log.search(query)
        more = len(records) > limit
        records = records[:limit]
        cursor = encode_cursor(records[-1].created_at, records[-1].id) if more else None
        return AuditPage(events=[AuditEventView.of(r) for r in records], next_cursor=cursor)

    async def _export(
        self,
        actor: Principal,
        scope: Scope,
        search: AuditSearch,
        *,
        tenant_id: UUID | None,
        platform_only: bool,
    ) -> list[AuditEventView]:
        if search.since is None or search.until is None:
            raise ValidationFailed(
                "Pick a start and end date for the download.", details={"field": "since"}
            )
        query = _filter(
            AuditSearch(
                actor_id=search.actor_id,
                action=search.action,
                target_type=search.target_type,
                target_id=search.target_id,
                since=search.since,
                until=search.until,
            ),
            tenant_id=tenant_id,
            platform_only=platform_only,
            limit=EXPORT_LIMIT + 1,
        )
        async with self._uow_factory(scope) as uow:
            records = await uow.audit_log.search(query)
            if len(records) > EXPORT_LIMIT:
                raise ValidationFailed(
                    f"That's more than {EXPORT_LIMIT:,} events. Pick a shorter date range.",
                    code="export_too_large",
                )
            uow.audit(
                audit.by(
                    actor,
                    "audit.exported",
                    target_type="audit_log",
                    target_id=str(tenant_id) if tenant_id else "platform",
                    tenant_id=tenant_id if actor.realm is Realm.SCHOOL else None,
                    rows=len(records),
                    since=search.since,
                    until=search.until,
                    action_filter=query.action_prefix,
                    actor_filter=search.actor_id,
                )
            )
            await uow.commit()
        return [AuditEventView.of(r) for r in records]
