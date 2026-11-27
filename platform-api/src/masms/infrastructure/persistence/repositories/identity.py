"""Identity repositories."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from masms.domain.identity.roles import Realm
from masms.domain.identity.session import RefreshSession
from masms.domain.identity.user import UserAccount, UserStatus
from masms.domain.shared.errors import Conflict
from masms.domain.shared.value_objects import EmailAddress
from masms.infrastructure.persistence.models.identity import (
    AuthSessionRow,
    PlatformUserRow,
    SchoolUserRow,
)
from masms.infrastructure.persistence.repositories.scope_state import ScopeState

_STALE = "This record was changed by someone else. Reload and try again."


def _school_to_domain(row: SchoolUserRow) -> UserAccount:
    return UserAccount(
        id=row.id,
        realm=Realm.SCHOOL,
        tenant_id=row.tenant_id,
        email=EmailAddress(row.email),
        full_name=row.full_name,
        password_hash=row.password_hash,
        roles=frozenset(row.roles),
        capabilities=frozenset(row.capabilities or ()),
        primary_owner=row.primary_owner,
        status=UserStatus(row.status),
        failed_login_count=row.failed_login_count,
        locked_until=row.locked_until,
        last_login_at=row.last_login_at,
        mfa_enabled=row.mfa_enabled,
        created_at=row.created_at,
        version=row.version,
    )


def _platform_to_domain(row: PlatformUserRow) -> UserAccount:
    return UserAccount(
        id=row.id,
        realm=Realm.PLATFORM,
        tenant_id=None,
        email=EmailAddress(row.email),
        full_name=row.full_name,
        password_hash=row.password_hash,
        roles=frozenset(row.roles),
        status=UserStatus(row.status),
        failed_login_count=row.failed_login_count,
        locked_until=row.locked_until,
        last_login_at=row.last_login_at,
        mfa_enabled=row.mfa_enabled,
        created_at=row.created_at,
        version=row.version,
    )


def _mutable_fields(user: UserAccount) -> dict[str, object]:
    return {
        "email": user.email.value,
        "full_name": user.full_name,
        "password_hash": user.password_hash,
        "roles": sorted(user.roles),
        "status": user.status.value,
        "failed_login_count": user.failed_login_count,
        "locked_until": user.locked_until,
        "last_login_at": user.last_login_at,
        "mfa_enabled": user.mfa_enabled,
    }


class SqlUserRepository:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    async def get(self, user_id: UUID) -> UserAccount | None:
        if self._scope.tenant_id is not None:
            row = await self._s.scalar(
                select(SchoolUserRow).where(
                    SchoolUserRow.tenant_id == self._scope.tenant_id, SchoolUserRow.id == user_id
                )
            )
            return _school_to_domain(row) if row else None
        if self._scope.platform:
            prow = await self._s.get(PlatformUserRow, user_id)
            return _platform_to_domain(prow) if prow else None
        return None

    async def get_school_user_by_email(
        self, tenant_id: UUID, email: EmailAddress
    ) -> UserAccount | None:
        row = await self._s.scalar(
            select(SchoolUserRow).where(
                SchoolUserRow.tenant_id == tenant_id, SchoolUserRow.email == email.value
            )
        )
        return _school_to_domain(row) if row else None

    async def get_platform_user_by_email(self, email: EmailAddress) -> UserAccount | None:
        row = await self._s.scalar(
            select(PlatformUserRow).where(PlatformUserRow.email == email.value)
        )
        return _platform_to_domain(row) if row else None

    async def add(self, user: UserAccount) -> None:
        fields = _mutable_fields(user) | {
            "id": user.id,
            "created_at": user.created_at,
            "version": user.version,
        }
        if user.realm is Realm.SCHOOL:
            self._s.add(
                SchoolUserRow(
                    tenant_id=user.tenant_id,
                    capabilities=sorted(user.capabilities),
                    primary_owner=user.primary_owner,
                    **fields,
                )
            )
        else:
            self._s.add(PlatformUserRow(**fields))
        await self._s.flush()

    async def save(self, user: UserAccount) -> None:
        """Optimistic concurrency: the update only applies if nobody saved a newer version first."""
        values = _mutable_fields(user) | {
            "version": user.version + 1,
            "updated_at": datetime.now(UTC),
        }
        if user.realm is Realm.SCHOOL:
            stmt = (
                update(SchoolUserRow)
                .where(
                    SchoolUserRow.tenant_id == user.tenant_id,
                    SchoolUserRow.id == user.id,
                    SchoolUserRow.version == user.version,
                )
                .values(
                    **values,
                    capabilities=sorted(user.capabilities),
                    primary_owner=user.primary_owner,
                )
            )
        else:
            stmt = (
                update(PlatformUserRow)
                .where(PlatformUserRow.id == user.id, PlatformUserRow.version == user.version)
                .values(**values)
            )
        result = await self._s.execute(stmt.execution_options(synchronize_session=False))
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise Conflict(_STALE)
        user.version += 1


def _session_to_domain(row: AuthSessionRow) -> RefreshSession:
    return RefreshSession(
        id=row.id,
        family_id=row.family_id,
        user_id=row.user_id,
        realm=Realm(row.realm),
        tenant_id=row.tenant_id,
        token_hash=row.token_hash,
        issued_at=row.issued_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        replaced_by=row.replaced_by,
        user_agent=row.user_agent,
        ip_address=str(row.ip_address) if row.ip_address else None,
    )


class SqlRefreshSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_token_hash(self, token_hash: str) -> RefreshSession | None:
        row = await self._s.scalar(
            select(AuthSessionRow).where(AuthSessionRow.token_hash == token_hash).with_for_update()
        )
        return _session_to_domain(row) if row else None

    async def add(self, session: RefreshSession) -> None:
        self._s.add(
            AuthSessionRow(
                id=session.id,
                family_id=session.family_id,
                user_id=session.user_id,
                realm=session.realm.value,
                tenant_id=session.tenant_id,
                token_hash=session.token_hash,
                issued_at=session.issued_at,
                expires_at=session.expires_at,
                user_agent=session.user_agent,
                ip_address=session.ip_address,
            )
        )
        await self._s.flush()

    async def save(self, session: RefreshSession) -> None:
        await self._s.execute(
            update(AuthSessionRow)
            .where(AuthSessionRow.id == session.id)
            .values(
                last_used_at=session.last_used_at,
                revoked_at=session.revoked_at,
                replaced_by=session.replaced_by,
            )
            .execution_options(synchronize_session=False)
        )

    async def revoke_family(self, family_id: UUID, now: datetime) -> int:
        result = await self._s.execute(
            update(AuthSessionRow)
            .where(AuthSessionRow.family_id == family_id, AuthSessionRow.revoked_at.is_(None))
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]
