"""Identity repositories."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from masms.domain.identity.guardians import GuardianLink, Relationship
from masms.domain.identity.invite import UserInvite
from masms.domain.identity.mfa import TotpFactor
from masms.domain.identity.roles import Realm
from masms.domain.identity.session import RefreshSession
from masms.domain.identity.user import UserAccount, UserStatus
from masms.domain.shared.errors import Conflict, ValidationFailed
from masms.domain.shared.value_objects import EmailAddress, Page
from masms.infrastructure.persistence.models.identity import (
    AuthSessionRow,
    GuardianLinkRow,
    PlatformMfaFactorRow,
    PlatformUserRow,
    SchoolUserRow,
    UserInviteRow,
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

    async def school_users_with_roles(
        self, tenant_id: UUID, roles: frozenset[str]
    ) -> list[UserAccount]:
        if not roles:
            return []
        rows = await self._s.scalars(
            select(SchoolUserRow)
            .where(
                SchoolUserRow.tenant_id == tenant_id,
                SchoolUserRow.roles.overlap(sorted(roles)),
                SchoolUserRow.status == UserStatus.ACTIVE.value,
            )
            .order_by(SchoolUserRow.full_name, SchoolUserRow.id)
            .limit(5000)
        )
        return [_school_to_domain(r) for r in rows]

    async def list_school_users(
        self,
        tenant_id: UUID,
        *,
        role: str | None,
        search: str | None,
        cursor: str | None,
        limit: int,
    ) -> Page[UserAccount]:
        stmt = (
            select(SchoolUserRow)
            .where(SchoolUserRow.tenant_id == tenant_id)
            .order_by(SchoolUserRow.id.desc())
            .limit(limit + 1)
        )
        if role:
            stmt = stmt.where(SchoolUserRow.roles.contains([role]))
        if search:
            pattern = f"%{search.replace('%', '').replace('_', '')}%"
            stmt = stmt.where(
                or_(SchoolUserRow.full_name.ilike(pattern), SchoolUserRow.email.ilike(pattern))
            )
        if cursor:
            try:
                after = UUID(bytes=base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            except (ValueError, TypeError) as exc:
                raise ValidationFailed("Cursor is not valid.", details={"field": "cursor"}) from exc
            stmt = stmt.where(SchoolUserRow.id < after)
        rows = list((await self._s.scalars(stmt)).all())
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = (
            base64.urlsafe_b64encode(rows[-1].id.bytes).decode().rstrip("=")
            if more and rows
            else None
        )
        return Page(items=[_school_to_domain(r) for r in rows], next_cursor=next_cursor)


class SqlInviteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, invite: UserInvite) -> None:
        self._s.add(
            UserInviteRow(
                tenant_id=invite.tenant_id,
                id=invite.id,
                user_id=invite.user_id,
                token_hash=invite.token_hash,
                expires_at=invite.expires_at,
            )
        )
        await self._s.flush()

    async def get_by_token_hash(self, tenant_id: UUID, token_hash: str) -> UserInvite | None:
        row = await self._s.scalar(
            select(UserInviteRow)
            .where(UserInviteRow.tenant_id == tenant_id, UserInviteRow.token_hash == token_hash)
            .with_for_update()
        )
        if row is None:
            return None
        return UserInvite(
            id=row.id,
            tenant_id=row.tenant_id,
            user_id=row.user_id,
            token_hash=row.token_hash,
            expires_at=row.expires_at,
            accepted_at=row.accepted_at,
        )

    async def save(self, invite: UserInvite) -> None:
        await self._s.execute(
            update(UserInviteRow)
            .where(UserInviteRow.tenant_id == invite.tenant_id, UserInviteRow.id == invite.id)
            .values(accepted_at=invite.accepted_at)
        )


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

    async def revoke_all_for_user(self, user_id: UUID, now: datetime) -> int:
        result = await self._s.execute(
            update(AuthSessionRow)
            .where(AuthSessionRow.user_id == user_id, AuthSessionRow.revoked_at.is_(None))
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]


class SqlMfaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, user_id: UUID) -> TotpFactor | None:
        row = await self._s.get(PlatformMfaFactorRow, user_id, with_for_update=True)
        if row is None:
            return None
        return TotpFactor(
            id=row.id,
            user_id=row.user_id,
            secret_ciphertext=row.secret_ciphertext,
            created_at=row.created_at,
            confirmed_at=row.confirmed_at,
            last_used_step=row.last_used_step,
            recovery_code_hashes=list(row.recovery_code_hashes),
        )

    async def upsert(self, factor: TotpFactor) -> None:
        values = {
            "id": factor.id,
            "secret_ciphertext": factor.secret_ciphertext,
            "created_at": factor.created_at,
            "confirmed_at": factor.confirmed_at,
            "last_used_step": factor.last_used_step,
            "recovery_code_hashes": list(factor.recovery_code_hashes),
        }
        stmt = pg_insert(PlatformMfaFactorRow).values(user_id=factor.user_id, **values)
        await self._s.execute(stmt.on_conflict_do_update(index_elements=["user_id"], set_=values))

    async def delete(self, user_id: UUID) -> None:
        await self._s.execute(
            sa_delete(PlatformMfaFactorRow).where(PlatformMfaFactorRow.user_id == user_id)
        )


def _link(row: GuardianLinkRow) -> GuardianLink:
    return GuardianLink(
        tenant_id=row.tenant_id,
        parent_id=row.parent_id,
        student_id=row.student_id,
        relationship=Relationship(row.relationship),
        created_by=row.created_by,
        created_at=row.created_at,
    )


class SqlGuardianRepository:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    @property
    def _tid(self) -> UUID:
        if self._scope.tenant_id is None:
            raise RuntimeError("Guardian links need a tenant scope.")
        return self._scope.tenant_id

    async def add(self, link: GuardianLink) -> bool:
        stmt = (
            pg_insert(GuardianLinkRow)
            .values(
                tenant_id=self._tid,
                parent_id=link.parent_id,
                student_id=link.student_id,
                relationship=link.relationship.value,
                created_by=link.created_by,
                created_at=link.created_at,
            )
            .on_conflict_do_nothing()
            .returning(GuardianLinkRow.parent_id)
        )
        return (await self._s.execute(stmt)).first() is not None

    async def remove(self, parent_id: UUID, student_id: UUID) -> bool:
        result = await self._s.execute(
            sa_delete(GuardianLinkRow)
            .where(
                GuardianLinkRow.tenant_id == self._tid,
                GuardianLinkRow.parent_id == parent_id,
                GuardianLinkRow.student_id == student_id,
            )
            .returning(GuardianLinkRow.parent_id)
        )
        return result.first() is not None

    async def guardians_of(self, student_id: UUID) -> list[GuardianLink]:
        rows = await self._s.scalars(
            select(GuardianLinkRow)
            .where(GuardianLinkRow.tenant_id == self._tid, GuardianLinkRow.student_id == student_id)
            .order_by(GuardianLinkRow.created_at)
        )
        return [_link(r) for r in rows]

    async def children_of(self, parent_id: UUID) -> list[GuardianLink]:
        rows = await self._s.scalars(
            select(GuardianLinkRow)
            .where(GuardianLinkRow.tenant_id == self._tid, GuardianLinkRow.parent_id == parent_id)
            .order_by(GuardianLinkRow.created_at)
        )
        return [_link(r) for r in rows]

    async def is_guardian(self, parent_id: UUID, student_id: UUID) -> bool:
        found = await self._s.scalar(
            select(GuardianLinkRow.parent_id).where(
                GuardianLinkRow.tenant_id == self._tid,
                GuardianLinkRow.parent_id == parent_id,
                GuardianLinkRow.student_id == student_id,
            )
        )
        return found is not None
