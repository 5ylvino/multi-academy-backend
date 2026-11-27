"""Tenancy repositories."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from masms.domain.shared.errors import ValidationFailed
from masms.domain.shared.value_objects import Page, Slug
from masms.domain.tenancy.tenant import SchoolLevel, Tenant, TenantStatus
from masms.domain.tenancy.verification import EmailVerification
from masms.infrastructure.persistence.models.tenancy import EmailVerificationRow, TenantRow


def _to_domain(row: TenantRow) -> Tenant:
    return Tenant(
        id=row.id,
        slug=Slug(row.slug),
        name=row.name,
        status=TenantStatus(row.status),
        status_reason=row.status_reason,
        plan_code=row.plan_code,
        school_levels=frozenset(SchoolLevel(v) for v in row.school_levels),
        country_code=row.country_code,
        contact_email=row.contact_email,
        created_at=row.created_at,
        trial_ends_at=row.trial_ends_at,
        config_version=row.config_version,
    )


def _encode_cursor(tenant_id: UUID) -> str:
    return base64.urlsafe_b64encode(tenant_id.bytes).decode().rstrip("=")


def _decode_cursor(cursor: str) -> UUID:
    try:
        return UUID(bytes=base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, TypeError) as exc:
        raise ValidationFailed("Cursor is not valid.", details={"field": "cursor"}) from exc


class SqlTenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, tenant_id: UUID) -> Tenant | None:
        row = await self._s.get(TenantRow, tenant_id)
        return _to_domain(row) if row else None

    async def get_by_slug(self, slug: Slug) -> Tenant | None:
        row = await self._s.scalar(select(TenantRow).where(TenantRow.slug == slug.value))
        return _to_domain(row) if row else None

    async def slug_exists(self, slug: Slug) -> bool:
        return bool(await self._s.scalar(select(exists().where(TenantRow.slug == slug.value))))

    async def add(self, tenant: Tenant) -> None:
        self._s.add(
            TenantRow(
                id=tenant.id,
                slug=tenant.slug.value,
                name=tenant.name,
                status=tenant.status.value,
                status_reason=tenant.status_reason,
                plan_code=tenant.plan_code,
                school_levels=sorted(level.value for level in tenant.school_levels),
                country_code=tenant.country_code,
                contact_email=tenant.contact_email,
                created_at=tenant.created_at,
                trial_ends_at=tenant.trial_ends_at,
                config_version=tenant.config_version,
            )
        )
        await self._s.flush()

    async def save(self, tenant: Tenant) -> None:
        await self._s.execute(
            update(TenantRow)
            .where(TenantRow.id == tenant.id)
            .values(
                name=tenant.name,
                status=tenant.status.value,
                status_reason=tenant.status_reason,
                plan_code=tenant.plan_code,
                school_levels=sorted(level.value for level in tenant.school_levels),
                trial_ends_at=tenant.trial_ends_at,
                config_version=tenant.config_version,
                updated_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )

    async def list(
        self, *, status: TenantStatus | None, search: str | None, cursor: str | None, limit: int
    ) -> Page[Tenant]:
        """Keyset pagination, newest first (UUIDv7 ids sort by creation time)."""
        stmt = select(TenantRow).order_by(TenantRow.id.desc()).limit(limit + 1)
        if status is not None:
            stmt = stmt.where(TenantRow.status == status.value)
        if search:
            pattern = f"%{search.replace('%', '').replace('_', '')}%"
            stmt = stmt.where(or_(TenantRow.name.ilike(pattern), TenantRow.slug.ilike(pattern)))
        if cursor:
            stmt = stmt.where(TenantRow.id < _decode_cursor(cursor))
        rows = list((await self._s.scalars(stmt)).all())
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_to_domain(r) for r in rows],
            next_cursor=_encode_cursor(rows[-1].id) if more and rows else None,
        )

    async def count_by_status(self) -> dict[str, int]:
        result = await self._s.execute(
            select(TenantRow.status, func.count()).group_by(TenantRow.status)
        )
        return {status: int(count) for status, count in result.all()}


class SqlEmailVerificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def latest_for_user(self, tenant_id: UUID, user_id: UUID) -> EmailVerification | None:
        row = await self._s.scalar(
            select(EmailVerificationRow)
            .where(
                EmailVerificationRow.tenant_id == tenant_id, EmailVerificationRow.user_id == user_id
            )
            .order_by(EmailVerificationRow.id.desc())
            .limit(1)
            .with_for_update()
        )
        if row is None:
            return None
        return EmailVerification(
            id=row.id,
            tenant_id=row.tenant_id,
            user_id=row.user_id,
            code_hash=row.code_hash,
            expires_at=row.expires_at,
            attempts=row.attempts,
            max_attempts=row.max_attempts,
            consumed_at=row.consumed_at,
        )

    async def add(self, verification: EmailVerification) -> None:
        self._s.add(
            EmailVerificationRow(
                id=verification.id,
                tenant_id=verification.tenant_id,
                user_id=verification.user_id,
                code_hash=verification.code_hash,
                expires_at=verification.expires_at,
                attempts=verification.attempts,
                max_attempts=verification.max_attempts,
            )
        )
        await self._s.flush()

    async def save(self, verification: EmailVerification) -> None:
        await self._s.execute(
            update(EmailVerificationRow)
            .where(
                EmailVerificationRow.tenant_id == verification.tenant_id,
                EmailVerificationRow.id == verification.id,
            )
            .values(attempts=verification.attempts, consumed_at=verification.consumed_at)
            .execution_options(synchronize_session=False)
        )
