"""School user management: invite people, accept invitations, list users."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from masms.domain.identity.events import UserInvited
from masms.domain.identity.invite import UserInvite
from masms.domain.identity.password_policy import validate_password
from masms.domain.identity.permissions import parse_school_roles, validate_custom_capabilities
from masms.domain.identity.roles import Realm, SchoolRole
from masms.domain.identity.user import UserAccount, UserStatus
from masms.domain.shared.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from masms.domain.shared.value_objects import EmailAddress, Page, Slug
from masms.use_cases.identity.session_issuer import ClientInfo, IssuedSession, SessionIssuer
from masms.use_cases.shared.pagination import clamp_limit
from masms.use_cases.shared.ports import (
    AuditEntry,
    Clock,
    PasswordHasher,
    RateLimiter,
    Scope,
    SecretGenerator,
    SecretHasher,
    UnitOfWorkFactory,
)
from masms.use_cases.shared.principal import Principal

#: Assigning these roles hands out administrative power, so it needs ``roles:assign``.
PRIVILEGED_ROLES = frozenset(
    {SchoolRole.DIRECTOR, SchoolRole.SCHOOL_ADMIN, SchoolRole.IT_ADMIN, SchoolRole.BURSAR}
)
_UNUSABLE_PASSWORD = "!invite-pending"  # noqa: S105 - not a hash any password can match


@dataclass(frozen=True, slots=True)
class SchoolUserView:
    id: UUID
    email: str
    full_name: str
    roles: list[str]
    capabilities: list[str]
    status: str
    last_login_at: datetime | None
    created_at: datetime

    @classmethod
    def of(cls, user: UserAccount) -> SchoolUserView:
        return cls(
            id=user.id,
            email=user.email.value,
            full_name=user.full_name,
            roles=sorted(user.roles),
            capabilities=sorted(user.capabilities),
            status=user.status.value,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
        )


@dataclass(frozen=True, slots=True)
class InviteSchoolUserCommand:
    email: str
    full_name: str
    roles: list[str]
    capabilities: list[str]


class InviteSchoolUser:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        secret_hasher: SecretHasher,
        secrets: SecretGenerator,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._secret_hasher = secret_hasher
        self._secrets = secrets
        self._clock = clock

    async def __call__(self, actor: Principal, cmd: InviteSchoolUserCommand) -> SchoolUserView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("users:create")
        tenant_id = actor.school_tenant_id
        roles = parse_school_roles(cmd.roles)
        capabilities = validate_custom_capabilities(cmd.capabilities)
        if (roles & PRIVILEGED_ROLES or capabilities) and not actor.permissions.allows(
            "roles:assign"
        ):
            raise PermissionDenied(
                "Only school leaders with role-assignment rights can grant these roles "
                "or capabilities.",
                details={"missing": ["roles:assign"]},
            )
        if not cmd.full_name.strip():
            raise ValidationFailed("Enter the person's name.", details={"field": "full_name"})
        email = EmailAddress(cmd.email)
        now = self._clock.now()
        token = self._secrets.token()

        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            tenant = await uow.tenants.get(tenant_id)
            if tenant is None:
                raise NotFound("School not found.")
            if await uow.users.get_school_user_by_email(tenant_id, email) is not None:
                raise Conflict(
                    "Someone at this school already uses this email.", details={"field": "email"}
                )
            user = UserAccount.create(
                realm=Realm.SCHOOL,
                tenant_id=tenant_id,
                email=email,
                full_name=cmd.full_name,
                password_hash=_UNUSABLE_PASSWORD,
                roles=frozenset(r.value for r in roles),
                capabilities=capabilities,
                status=UserStatus.PENDING,
                now=now,
            )
            await uow.users.add(user)
            uow.track(user)
            await uow.invites.add(
                UserInvite.issue(
                    tenant_id=tenant_id,
                    user_id=user.id,
                    token_hash=self._secret_hasher.hash(token),
                    now=now,
                )
            )
            uow.publish(
                UserInvited(
                    tenant_id=tenant_id,
                    user_id=user.id,
                    email=email.value,
                    full_name=user.full_name,
                    school_name=tenant.name,
                    school_slug=tenant.slug.value,
                    token=token,
                )
            )
            uow.audit(
                AuditEntry(
                    action="school_user.invited",
                    actor_id=actor.user_id,
                    actor_realm=Realm.SCHOOL,
                    tenant_id=tenant_id,
                    target_type="school_user",
                    target_id=str(user.id),
                    metadata={"roles": sorted(user.roles), "capabilities": sorted(capabilities)},
                )
            )
            await uow.commit()
        return SchoolUserView.of(user)


@dataclass(frozen=True, slots=True)
class AcceptInviteCommand:
    school_slug: str
    token: str
    password: str
    client: ClientInfo


class AcceptInvite:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        hasher: PasswordHasher,
        secret_hasher: SecretHasher,
        issuer: SessionIssuer,
        limiter: RateLimiter,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._hasher = hasher
        self._secret_hasher = secret_hasher
        self._issuer = issuer
        self._limiter = limiter
        self._clock = clock

    async def __call__(self, cmd: AcceptInviteCommand) -> IssuedSession:
        await self._limiter.hit(f"invite:ip:{cmd.client.ip_address}", limit=20, window_seconds=600)
        now = self._clock.now()
        async with self._uow_factory(Scope.registry()) as uow:
            tenant = await uow.tenants.get_by_slug(Slug(cmd.school_slug))
            if tenant is None or not tenant.is_serving:
                raise NotFound("This invitation is not valid.")
            await uow.enter_tenant(tenant.id)
            invite = await uow.invites.get_by_token_hash(
                tenant.id, self._secret_hasher.hash(cmd.token)
            )
            if invite is None:
                raise NotFound("This invitation is not valid.")
            user = await uow.users.get(invite.user_id)
            if user is None:
                raise NotFound("This invitation is not valid.")
            validate_password(cmd.password, email=user.email.value)
            invite.accept(now)
            await uow.invites.save(invite)
            user.change_password_hash(await self._hasher.hash(cmd.password))
            user.activate()
            user.record_successful_sign_in(now)
            await uow.users.save(user)
            issued, _ = await self._issuer.issue(
                uow, user, client=cmd.client, tenant_config_version=tenant.config_version
            )
            await uow.commit()
            return issued


class ListSchoolUsers:
    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def __call__(
        self,
        actor: Principal,
        *,
        role: str | None,
        search: str | None,
        cursor: str | None,
        limit: int | None,
    ) -> Page[SchoolUserView]:
        actor.require_realm(Realm.SCHOOL)
        actor.require("users:read")
        if role is not None:
            parse_school_roles([role])
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            page = await uow.users.list_school_users(
                tenant_id,
                role=role,
                search=(search or "").strip() or None,
                cursor=cursor,
                limit=clamp_limit(limit),
            )
        return Page(items=[SchoolUserView.of(u) for u in page.items], next_cursor=page.next_cursor)
