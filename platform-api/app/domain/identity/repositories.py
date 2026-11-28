"""Repository ports for identity. Implemented in infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.identity.guardians import GuardianLink
from app.domain.identity.invite import UserInvite
from app.domain.identity.mfa import TotpFactor
from app.domain.identity.session import RefreshSession
from app.domain.identity.user import UserAccount
from app.domain.shared.value_objects import EmailAddress, Page


@dataclass(frozen=True, slots=True)
class SectionFilter:
    """People a section's staff may see: staff of those sections, staff who serve the whole
    school (no section roles, not in any section), students in classes of those levels this
    session, and those students' guardians."""

    section_ids: frozenset[UUID]
    levels: frozenset[str]
    section_roles: frozenset[str]
    session_id: UUID | None


class UserRepository(Protocol):
    """School users are read inside the current tenant scope; platform users have none."""

    async def get(self, user_id: UUID) -> UserAccount | None: ...

    async def get_school_user_by_email(
        self, tenant_id: UUID, email: EmailAddress
    ) -> UserAccount | None: ...

    async def get_platform_user_by_email(self, email: EmailAddress) -> UserAccount | None: ...

    async def add(self, user: UserAccount) -> None: ...

    async def save(self, user: UserAccount) -> None: ...

    async def school_users_with_access(
        self, tenant_id: UUID, *, roles: frozenset[str], capability: str
    ) -> list[UserAccount]:
        """Active users holding any of ``roles``, the ``capability``, or primary ownership."""
        ...

    async def school_names(self, user_ids: set[UUID]) -> dict[UUID, str]:
        """Full names of school users in the current scope, for lists and exports."""
        ...

    async def school_users_with_roles(
        self, tenant_id: UUID, roles: frozenset[str]
    ) -> list[UserAccount]:
        """Active users holding any of ``roles``, by name. For registers, not for listing."""
        ...

    async def list_school_users(
        self,
        tenant_id: UUID,
        *,
        role: str | None,
        search: str | None,
        cursor: str | None,
        limit: int,
        within: SectionFilter | None = None,
    ) -> Page[UserAccount]:
        """``within`` limits the list to one part of the school (see ``SectionFilter``)."""
        ...


class InviteRepository(Protocol):
    async def add(self, invite: UserInvite) -> None: ...

    async def get_by_token_hash(self, tenant_id: UUID, token_hash: str) -> UserInvite | None: ...

    async def save(self, invite: UserInvite) -> None: ...


class RefreshSessionRepository(Protocol):
    async def get_by_token_hash(self, token_hash: str) -> RefreshSession | None: ...

    async def add(self, session: RefreshSession) -> None: ...

    async def save(self, session: RefreshSession) -> None: ...

    async def revoke_family(self, family_id: UUID, now: datetime) -> int: ...

    async def revoke_all_for_user(self, user_id: UUID, now: datetime) -> int: ...


class MfaRepository(Protocol):
    async def get(self, user_id: UUID) -> TotpFactor | None: ...

    async def upsert(self, factor: TotpFactor) -> None: ...

    async def delete(self, user_id: UUID) -> None: ...


class GuardianRepository(Protocol):
    """Scoped to the current school."""

    async def add(self, link: GuardianLink) -> bool:
        """False if the link already exists."""
        ...

    async def remove(self, parent_id: UUID, student_id: UUID) -> bool: ...

    async def guardians_of(self, student_id: UUID) -> list[GuardianLink]: ...

    async def children_of(self, parent_id: UUID) -> list[GuardianLink]: ...

    async def is_guardian(self, parent_id: UUID, student_id: UUID) -> bool: ...
