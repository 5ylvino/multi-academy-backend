"""Profile of the signed-in user."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from masms.domain.identity.roles import Realm
from masms.domain.shared.errors import NotFound
from masms.use_cases.shared.ports import Scope, UnitOfWorkFactory
from masms.use_cases.shared.principal import Principal


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: UUID
    realm: str
    tenant_id: UUID | None
    email: str
    full_name: str
    roles: list[str]
    permissions: list[str]
    primary_owner: bool
    last_login_at: datetime | None


class GetCurrentUser:
    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def __call__(self, principal: Principal) -> CurrentUser:
        scope = (
            Scope.platform_wide()
            if principal.realm is Realm.PLATFORM
            else Scope.tenant(principal.school_tenant_id)
        )
        async with self._uow_factory(scope, read_only=True) as uow:
            user = await uow.users.get(principal.user_id)
        if user is None:
            raise NotFound("Account not found.")
        return CurrentUser(
            id=user.id,
            realm=user.realm.value,
            tenant_id=user.tenant_id,
            email=user.email.value,
            full_name=user.full_name,
            roles=sorted(user.roles),
            permissions=sorted(user.permissions.grants),
            primary_owner=user.primary_owner,
            last_login_at=user.last_login_at,
        )
