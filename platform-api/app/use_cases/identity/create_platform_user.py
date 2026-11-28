"""Create a platform (owner console) staff account. Used by the CLI and by platform admins."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.identity.password_policy import validate_password
from app.domain.identity.roles import PlatformRole, Realm
from app.domain.identity.user import UserAccount
from app.domain.shared.errors import Conflict, ValidationFailed
from app.domain.shared.value_objects import EmailAddress
from app.use_cases.shared.ports import AuditEntry, Clock, PasswordHasher, Scope, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal


@dataclass(frozen=True, slots=True)
class CreatePlatformUserCommand:
    email: str
    full_name: str
    password: str
    roles: list[str]


class CreatePlatformUser:
    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, hasher: PasswordHasher, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._hasher = hasher
        self._clock = clock

    async def __call__(self, cmd: CreatePlatformUserCommand, actor: Principal | None) -> UUID:
        """``actor`` is None only for the bootstrap CLI, run with shell access to the server."""
        if actor is not None:
            actor.require_realm(Realm.PLATFORM)
            actor.require("platform_users:write")
        try:
            roles = frozenset(PlatformRole(r).value for r in cmd.roles)
        except ValueError as exc:
            raise ValidationFailed("Unknown platform role.", details={"roles": cmd.roles}) from exc
        if not roles:
            raise ValidationFailed("At least one role is required.", details={"field": "roles"})
        email = EmailAddress(cmd.email)
        validate_password(cmd.password, email=email.value)
        password_hash = await self._hasher.hash(cmd.password)

        async with self._uow_factory(Scope.platform_wide()) as uow:
            if await uow.users.get_platform_user_by_email(email) is not None:
                raise Conflict("A platform account with this email already exists.")
            user = UserAccount.create(
                realm=Realm.PLATFORM,
                tenant_id=None,
                email=email,
                full_name=cmd.full_name,
                password_hash=password_hash,
                roles=roles,
                now=self._clock.now(),
            )
            await uow.users.add(user)
            uow.track(user)
            uow.audit(
                AuditEntry(
                    action="platform_user.created",
                    actor_id=actor.user_id if actor else None,
                    actor_realm=Realm.PLATFORM if actor else None,
                    tenant_id=None,
                    target_type="platform_user",
                    target_id=str(user.id),
                    metadata={"roles": sorted(roles)},
                )
            )
            await uow.commit()
            return user.id
