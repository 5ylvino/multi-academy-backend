"""Audit trail search. Rows are written by the unit of work; this side only reads them."""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.persistence.models.identity import PlatformUserRow, SchoolUserRow
from app.infrastructure.persistence.models.system import AuditEventRow
from app.use_cases.shared.ports import AuditFilter, AuditRecord


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SqlAuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def search(self, query: AuditFilter) -> list[AuditRecord]:
        a = AuditEventRow
        school = SchoolUserRow
        platform = PlatformUserRow
        stmt = (
            select(
                a,
                school.full_name,
                school.email,
                platform.full_name,
                platform.email,
            )
            .outerjoin(
                school,
                and_(
                    a.actor_realm == "school",
                    school.tenant_id == a.tenant_id,
                    school.id == a.actor_id,
                ),
            )
            .outerjoin(platform, and_(a.actor_realm == "platform", platform.id == a.actor_id))
        )
        if query.tenant_id is not None:
            stmt = stmt.where(a.tenant_id == query.tenant_id)
        elif query.platform_only:
            stmt = stmt.where(a.tenant_id.is_(None))
        if query.actor_id is not None:
            stmt = stmt.where(a.actor_id == query.actor_id)
        if query.action_prefix:
            stmt = stmt.where(a.action.like(_escape_like(query.action_prefix) + "%", escape="\\"))
        if query.target_type:
            stmt = stmt.where(a.target_type == query.target_type)
        if query.target_id:
            stmt = stmt.where(a.target_id == query.target_id)
        if query.since is not None:
            stmt = stmt.where(a.created_at >= query.since)
        if query.until is not None:
            stmt = stmt.where(a.created_at < query.until)
        if query.before is not None:
            at, event_id = query.before
            stmt = stmt.where(or_(a.created_at < at, and_(a.created_at == at, a.id < event_id)))
        stmt = stmt.order_by(a.created_at.desc(), a.id.desc()).limit(query.limit)

        records: list[AuditRecord] = []
        for row, s_name, s_email, p_name, p_email in (await self._s.execute(stmt)).all():
            records.append(
                AuditRecord(
                    id=row.id,
                    created_at=row.created_at,
                    tenant_id=row.tenant_id,
                    actor_id=row.actor_id,
                    actor_realm=row.actor_realm,
                    actor_name=s_name or p_name,
                    actor_email=s_email or p_email,
                    action=row.action,
                    target_type=row.target_type,
                    target_id=row.target_id,
                    metadata=row.metadata_ or {},
                    request_id=row.request_id,
                    ip_address=row.ip_address,
                    user_agent=row.user_agent,
                )
            )
        return records
