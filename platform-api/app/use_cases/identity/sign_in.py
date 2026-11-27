"""Sign in with email and password, for school users or platform staff."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.identity.roles import Realm
from app.domain.identity.user import LoginPolicy, UserAccount
from app.domain.shared.errors import AuthenticationFailed, TenantUnavailable
from app.domain.shared.value_objects import EmailAddress, Slug
from app.use_cases.identity.mfa import ChallengePurpose, MfaChallenge, MfaChallenges
from app.use_cases.identity.session_issuer import ClientInfo, IssuedSession, SessionIssuer
from app.use_cases.shared.ports import (
    Clock,
    PasswordHasher,
    RateLimiter,
    Scope,
    UnitOfWork,
    UnitOfWorkFactory,
)

_BAD_CREDENTIALS = "Email or password is incorrect."


@dataclass(frozen=True, slots=True)
class SignInCommand:
    realm: Realm
    email: str
    password: str
    school_slug: str | None
    client: ClientInfo


class SignIn:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        hasher: PasswordHasher,
        issuer: SessionIssuer,
        limiter: RateLimiter,
        clock: Clock,
        policy: LoginPolicy,
        mfa: MfaChallenges | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._hasher = hasher
        self._issuer = issuer
        self._limiter = limiter
        self._clock = clock
        self._policy = policy
        self._mfa = mfa

    async def __call__(self, cmd: SignInCommand) -> IssuedSession | MfaChallenge:
        """A session, or, when a second factor is required, a challenge to complete it with."""
        email = EmailAddress(cmd.email)
        await self._limiter.hit(f"signin:ip:{cmd.client.ip_address}", limit=60, window_seconds=60)
        await self._limiter.hit(f"signin:email:{cmd.realm}:{email}", limit=10, window_seconds=300)

        scope = Scope.platform_wide() if cmd.realm is Realm.PLATFORM else Scope.registry()
        async with self._uow_factory(scope) as uow:
            user, config_version = await self._find_user(uow, cmd, email)
            if user is None:
                await self._hasher.burn_time()
                raise AuthenticationFailed(_BAD_CREDENTIALS)

            now = self._clock.now()
            user.ensure_can_sign_in(now)
            if not await self._hasher.verify(user.password_hash, cmd.password):
                user.record_failed_sign_in(now, self._policy)
                uow.track(user)
                await uow.users.save(user)
                await uow.commit()
                raise AuthenticationFailed(_BAD_CREDENTIALS)

            rehashed = self._hasher.needs_rehash(user.password_hash)
            if rehashed:
                user.change_password_hash(await self._hasher.hash(cmd.password))
            if self._mfa is not None:
                # The failure counter is reset only once the second factor passes.
                if rehashed:
                    await uow.users.save(user)
                    await uow.commit()
                purpose = ChallengePurpose.VERIFY if user.mfa_enabled else ChallengePurpose.ENROL
                return await self._mfa.open(user.id, purpose)
            user.record_successful_sign_in(now)
            await uow.users.save(user)
            issued, _ = await self._issuer.issue(
                uow, user, client=cmd.client, tenant_config_version=config_version
            )
            await uow.commit()
            return issued

    async def _find_user(
        self, uow: UnitOfWork, cmd: SignInCommand, email: EmailAddress
    ) -> tuple[UserAccount | None, int | None]:
        if cmd.realm is Realm.PLATFORM:
            return await uow.users.get_platform_user_by_email(email), None
        if not cmd.school_slug:
            raise AuthenticationFailed(_BAD_CREDENTIALS)
        tenant = await uow.tenants.get_by_slug(Slug(cmd.school_slug))
        if tenant is None:
            return None, None
        if not tenant.is_serving:
            raise TenantUnavailable(
                "This school account is not available. Contact your school administrator.",
                details={"status": tenant.status.value},
            )
        await uow.enter_tenant(tenant.id)
        return await uow.users.get_school_user_by_email(tenant.id, email), tenant.config_version
