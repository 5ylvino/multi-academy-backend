"""Verify the founding administrator's email, start the trial and sign them in."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from masms.domain.shared.errors import NotFound, ValidationFailed
from masms.domain.shared.value_objects import EmailAddress, Slug
from masms.domain.tenancy.events import VerificationCodeIssued
from masms.domain.tenancy.tenant import TenantStatus
from masms.domain.tenancy.verification import EmailVerification
from masms.use_cases.identity.session_issuer import ClientInfo, IssuedSession, SessionIssuer
from masms.use_cases.shared.ports import (
    Clock,
    RateLimiter,
    Scope,
    SecretGenerator,
    SecretHasher,
    UnitOfWorkFactory,
)
from masms.use_cases.tenancy.register_school import VERIFICATION_TTL

_NOT_PENDING = "This school is already verified. Sign in instead."


@dataclass(frozen=True, slots=True)
class VerifySchoolEmailCommand:
    slug: str
    email: str
    code: str
    client: ClientInfo


class VerifySchoolEmail:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        secret_hasher: SecretHasher,
        issuer: SessionIssuer,
        limiter: RateLimiter,
        clock: Clock,
        trial_length: timedelta,
    ) -> None:
        self._uow_factory = uow_factory
        self._secret_hasher = secret_hasher
        self._issuer = issuer
        self._limiter = limiter
        self._clock = clock
        self._trial_length = trial_length

    async def __call__(self, cmd: VerifySchoolEmailCommand) -> IssuedSession:
        await self._limiter.hit(f"verify:ip:{cmd.client.ip_address}", limit=30, window_seconds=600)
        email = EmailAddress(cmd.email)
        now = self._clock.now()
        async with self._uow_factory(Scope.registry()) as uow:
            tenant = await uow.tenants.get_by_slug(Slug(cmd.slug))
            if tenant is None:
                raise NotFound("School not found.")
            if tenant.status is not TenantStatus.PENDING_VERIFICATION:
                raise ValidationFailed(_NOT_PENDING, code="already_verified")
            await uow.enter_tenant(tenant.id)
            user = await uow.users.get_school_user_by_email(tenant.id, email)
            if user is None:
                raise NotFound("School not found.")
            verification = await uow.verifications.latest_for_user(tenant.id, user.id)
            if verification is None:
                raise ValidationFailed("Request a new code.", code="verification_missing")
            verification.ensure_usable(now)
            if not self._secret_hasher.matches(cmd.code.strip(), verification.code_hash):
                verification.record_wrong_code()
                await uow.verifications.save(verification)
                await uow.commit()
                raise ValidationFailed("That code is not right.", code="verification_wrong")

            verification.consume(now)
            await uow.verifications.save(verification)
            user.activate()
            user.record_successful_sign_in(now)
            await uow.users.save(user)
            tenant.start_trial(now, self._trial_length)
            await uow.tenants.save(tenant)
            uow.track(tenant)
            issued, _ = await self._issuer.issue(
                uow, user, client=cmd.client, tenant_config_version=tenant.config_version
            )
            await uow.commit()
            return issued


@dataclass(frozen=True, slots=True)
class ResendVerificationCommand:
    slug: str
    email: str
    client_ip: str | None


class ResendVerification:
    """Always succeeds from the caller's view, so it cannot be used to probe which emails exist."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        secret_hasher: SecretHasher,
        secrets: SecretGenerator,
        limiter: RateLimiter,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._secret_hasher = secret_hasher
        self._secrets = secrets
        self._limiter = limiter
        self._clock = clock

    async def __call__(self, cmd: ResendVerificationCommand) -> None:
        email = EmailAddress(cmd.email)
        await self._limiter.hit(f"resend:ip:{cmd.client_ip}", limit=10, window_seconds=3600)
        await self._limiter.hit(f"resend:email:{email}", limit=3, window_seconds=900)
        now = self._clock.now()
        async with self._uow_factory(Scope.registry()) as uow:
            tenant = await uow.tenants.get_by_slug(Slug(cmd.slug))
            if tenant is None or tenant.status is not TenantStatus.PENDING_VERIFICATION:
                return
            await uow.enter_tenant(tenant.id)
            user = await uow.users.get_school_user_by_email(tenant.id, email)
            if user is None:
                return
            code = self._secrets.numeric_code(6)
            await uow.verifications.add(
                EmailVerification.issue(
                    tenant_id=tenant.id,
                    user_id=user.id,
                    code_hash=self._secret_hasher.hash(code),
                    now=now,
                    ttl=VERIFICATION_TTL,
                )
            )
            uow.publish(
                VerificationCodeIssued(
                    tenant_id=tenant.id, email=email.value, school_name=tenant.name, code=code
                )
            )
            await uow.commit()
