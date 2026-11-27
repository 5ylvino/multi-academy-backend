"""Repository ports for identity. Implemented in infrastructure."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from masms.domain.identity.session import RefreshSession
from masms.domain.identity.user import UserAccount
from masms.domain.shared.value_objects import EmailAddress


class UserRepository(Protocol):
    """School users are read inside the current tenant scope; platform users have none."""

    async def get(self, user_id: UUID) -> UserAccount | None: ...

    async def get_school_user_by_email(
        self, tenant_id: UUID, email: EmailAddress
    ) -> UserAccount | None: ...

    async def get_platform_user_by_email(self, email: EmailAddress) -> UserAccount | None: ...

    async def add(self, user: UserAccount) -> None: ...

    async def save(self, user: UserAccount) -> None: ...


class RefreshSessionRepository(Protocol):
    async def get_by_token_hash(self, token_hash: str) -> RefreshSession | None: ...

    async def add(self, session: RefreshSession) -> None: ...

    async def save(self, session: RefreshSession) -> None: ...

    async def revoke_family(self, family_id: UUID, now: datetime) -> int: ...
