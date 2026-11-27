"""Register a new school and its founding administrator.

The school starts in ``pending_verification``. A 6-digit code is emailed to the administrator;
verifying it starts the free trial and signs them in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from app.domain.identity.password_policy import validate_password
from app.domain.identity.roles import Realm, SchoolRole
from app.domain.identity.user import UserAccount, UserStatus
from app.domain.shared.errors import Conflict, ValidationFailed
from app.domain.shared.value_objects import EmailAddress, Slug
from app.domain.tenancy.events import VerificationCodeIssued
from app.domain.tenancy.tenant import SchoolLevel, Tenant
from app.domain.tenancy.verification import EmailVerification
from app.use_cases.shared.ports import (
    Clock,
    PasswordHasher,
    RateLimiter,
    Scope,
    SecretGenerator,
    SecretHasher,
    UnitOfWorkFactory,
)

VERIFICATION_TTL = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class RegisterSchoolCommand:
    school_name: str
    slug: str | None
    school_levels: list[str]
    country_code: str
    admin_full_name: str
    admin_email: str
    admin_password: str
    client_ip: str | None
    timezone: str | None = None


@dataclass(frozen=True, slots=True)
class RegisteredSchool:
    tenant_id: UUID
    slug: str
    status: str
    verification_expires_in: int


class RegisterSchool:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        hasher: PasswordHasher,
        secret_hasher: SecretHasher,
        secrets: SecretGenerator,
        limiter: RateLimiter,
        clock: Clock,
        default_plan: str,
    ) -> None:
        self._uow_factory = uow_factory
        self._hasher = hasher
        self._secret_hasher = secret_hasher
        self._secrets = secrets
        self._limiter = limiter
        self._clock = clock
        self._default_plan = default_plan

    async def __call__(self, cmd: RegisterSchoolCommand) -> RegisteredSchool:
        await self._limiter.hit(f"register:ip:{cmd.client_ip}", limit=5, window_seconds=3600)
        email = EmailAddress(cmd.admin_email)
        validate_password(cmd.admin_password, email=email.value)
        slug = Slug(cmd.slug) if cmd.slug else Slug.from_name(cmd.school_name)
        try:
            levels = frozenset(SchoolLevel(v) for v in cmd.school_levels)
        except ValueError as exc:
            raise ValidationFailed(
                "Unknown school level.", details={"field": "school_levels"}
            ) from exc
        if not cmd.admin_full_name.strip():
            raise ValidationFailed(
                "Enter the administrator's name.", details={"field": "admin_full_name"}
            )

        password_hash = await self._hasher.hash(cmd.admin_password)
        now = self._clock.now()
        code = self._secrets.numeric_code(6)

        async with self._uow_factory(Scope.registry()) as uow:
            if await uow.tenants.slug_exists(slug):
                raise Conflict("That school link is taken. Try another.", details={"field": "slug"})
            tenant = Tenant.register(
                name=cmd.school_name,
                slug=slug,
                school_levels=levels,
                country_code=cmd.country_code,
                timezone=cmd.timezone,
                contact_email=email.value,
                plan_code=self._default_plan,
                now=now,
            )
            await uow.tenants.add(tenant)
            uow.track(tenant)

            await uow.enter_tenant(tenant.id)
            admin = UserAccount.create(
                realm=Realm.SCHOOL,
                tenant_id=tenant.id,
                email=email,
                full_name=cmd.admin_full_name,
                password_hash=password_hash,
                roles=frozenset({SchoolRole.DIRECTOR.value}),
                primary_owner=True,
                status=UserStatus.PENDING,
                now=now,
            )
            await uow.users.add(admin)
            uow.track(admin)

            verification = EmailVerification.issue(
                tenant_id=tenant.id,
                user_id=admin.id,
                code_hash=self._secret_hasher.hash(code),
                now=now,
                ttl=VERIFICATION_TTL,
            )
            await uow.verifications.add(verification)
            uow.publish(
                VerificationCodeIssued(
                    tenant_id=tenant.id, email=email.value, school_name=tenant.name, code=code
                )
            )
            await uow.commit()

        return RegisteredSchool(
            tenant_id=tenant.id,
            slug=tenant.slug.value,
            status=tenant.status.value,
            verification_expires_in=int(VERIFICATION_TTL.total_seconds()),
        )
