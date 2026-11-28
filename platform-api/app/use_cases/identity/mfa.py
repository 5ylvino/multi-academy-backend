"""Two-step verification for owner-console staff: authenticator app plus recovery codes.

Sign-in becomes two steps. A correct password returns a short-lived, single-use challenge
instead of a session; the session is issued only after a valid second factor. Staff without a
factor yet are sent through enrolment, so no console session ever exists without MFA.

Lockout: a wrong second-factor code counts as a failed sign-in, and a correct password alone
does not reset the counter, so a stolen password can't be used to guess codes indefinitely.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.domain.identity import totp
from app.domain.identity.mfa import (
    RECOVERY_ALPHABET,
    RECOVERY_CODE_COUNT,
    TotpFactor,
    looks_like_recovery_code,
    normalise_recovery_code,
)
from app.domain.identity.roles import Realm
from app.domain.identity.user import LoginPolicy, UserAccount
from app.domain.shared.errors import (
    AuthenticationFailed,
    Conflict,
    InvalidStateTransition,
    NotFound,
    PermissionDenied,
)
from app.domain.shared.value_objects import EmailAddress
from app.use_cases.identity import sign_in_audit
from app.use_cases.identity.session_issuer import ClientInfo, IssuedSession, SessionIssuer
from app.use_cases.shared.ports import (
    AuditEntry,
    Cache,
    Clock,
    RateLimiter,
    Scope,
    SecretBox,
    SecretGenerator,
    SecretHasher,
    UnitOfWork,
    UnitOfWorkFactory,
)
from app.use_cases.shared.principal import Principal

CHALLENGE_TTL_SECONDS = 300
MAX_CHALLENGE_ATTEMPTS = 5
SECRET_BYTES = 20  # 160 bits, the RFC 4226 recommendation
_BAD_CODE = "That code didn't work. Check your authenticator app and try again."
_BAD_CHALLENGE = "Your sign-in expired. Sign in again."


class ChallengePurpose(StrEnum):
    VERIFY = "verify"
    ENROL = "enrol"


@dataclass(frozen=True, slots=True)
class MfaChallenge:
    purpose: ChallengePurpose
    token: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class _ChallengeState:
    key: str
    user_id: UUID
    purpose: ChallengePurpose
    attempts: int


class MfaChallenges:
    """Short-lived challenges in the cache, keyed by the HMAC of the token (never the token)."""

    def __init__(self, *, cache: Cache, secrets: SecretGenerator, secret_hasher: SecretHasher):
        self._cache = cache
        self._secrets = secrets
        self._hasher = secret_hasher

    def _key(self, token: str) -> str:
        return f"mfa:challenge:{self._hasher.hash(token)}"

    async def open(self, user_id: UUID, purpose: ChallengePurpose) -> MfaChallenge:
        token = self._secrets.token()
        await self._cache.set_json(
            self._key(token),
            {"user_id": str(user_id), "purpose": purpose.value, "attempts": 0},
            CHALLENGE_TTL_SECONDS,
        )
        return MfaChallenge(purpose=purpose, token=token, expires_in=CHALLENGE_TTL_SECONDS)

    async def load(self, token: str, purpose: ChallengePurpose) -> _ChallengeState:
        key = self._key(token)
        raw: dict[str, Any] | None = await self._cache.get_json(key)
        if not raw or raw.get("purpose") != purpose.value:
            raise AuthenticationFailed(_BAD_CHALLENGE, code="mfa_challenge_invalid")
        return _ChallengeState(
            key=key,
            user_id=UUID(str(raw["user_id"])),
            purpose=purpose,
            attempts=int(raw.get("attempts", 0)),
        )

    async def failed(self, state: _ChallengeState) -> None:
        attempts = state.attempts + 1
        if attempts >= MAX_CHALLENGE_ATTEMPTS:
            await self._cache.delete(state.key)
            return
        await self._cache.set_json(
            state.key,
            {"user_id": str(state.user_id), "purpose": state.purpose.value, "attempts": attempts},
            CHALLENGE_TTL_SECONDS,
        )

    async def close(self, state: _ChallengeState) -> None:
        await self._cache.delete(state.key)


def _audit(action: str, user: UserAccount, actor_id: UUID | None, **meta: Any) -> AuditEntry:
    return AuditEntry(
        action=action,
        actor_id=actor_id,
        actor_realm=Realm.PLATFORM,
        tenant_id=None,
        target_type="platform_user",
        target_id=str(user.id),
        metadata=meta,
    )


class MfaUseCase:
    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        challenges: MfaChallenges,
        box: SecretBox,
        secrets: SecretGenerator,
        secret_hasher: SecretHasher,
        issuer: SessionIssuer,
        limiter: RateLimiter,
        clock: Clock,
        policy: LoginPolicy,
        totp_issuer: str,
    ) -> None:
        self._uow_factory = uow_factory
        self._challenges = challenges
        self._box = box
        self._secrets = secrets
        self._secret_hasher = secret_hasher
        self._issuer = issuer
        self._limiter = limiter
        self._clock = clock
        self._policy = policy
        self._totp_issuer = totp_issuer

    async def _user(self, uow: UnitOfWork, user_id: UUID) -> UserAccount:
        user = await uow.users.get(user_id)
        if user is None or user.realm is not Realm.PLATFORM:
            raise AuthenticationFailed(_BAD_CHALLENGE, code="mfa_challenge_invalid")
        user.ensure_can_sign_in(self._clock.now())
        return user

    async def _reject(
        self, uow: UnitOfWork, user: UserAccount, state: _ChallengeState | None
    ) -> AuthenticationFailed:
        user.record_failed_sign_in(self._clock.now(), self._policy)
        uow.track(user)
        await uow.users.save(user)
        uow.audit(sign_in_audit.sign_in_failed(user, reason="wrong_mfa_code"))
        await uow.commit()
        if state is not None:
            await self._challenges.failed(state)
        return AuthenticationFailed(_BAD_CODE, code="mfa_code_invalid")

    def _new_recovery_codes(self) -> tuple[list[str], list[str]]:
        codes = [
            self._secrets.code_from_alphabet(RECOVERY_ALPHABET, 10)
            for _ in range(RECOVERY_CODE_COUNT)
        ]
        shown = [f"{c[:5]}-{c[5:]}" for c in codes]
        return shown, [self._secret_hasher.hash(c) for c in codes]

    def _totp_step(self, factor: TotpFactor, code: str) -> int | None:
        secret = self._box.decrypt(factor.secret_ciphertext)
        return totp.matching_step(
            secret, code.strip(), self._clock.now(), after_step=factor.last_used_step
        )


# ---- sign-in, second step ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerifyMfaCommand:
    challenge_token: str
    code: str
    client: ClientInfo


class VerifyMfaSignIn(MfaUseCase):
    """Finish sign-in with an authenticator code or a one-time recovery code."""

    async def __call__(self, cmd: VerifyMfaCommand) -> IssuedSession:
        await self._limiter.hit(f"mfa:ip:{cmd.client.ip_address}", limit=30, window_seconds=300)
        state = await self._challenges.load(cmd.challenge_token, ChallengePurpose.VERIFY)
        async with self._uow_factory(Scope.platform_wide()) as uow:
            user = await self._user(uow, state.user_id)
            factor = await uow.mfa.get(user.id)
            if factor is None or not factor.confirmed:
                raise AuthenticationFailed(_BAD_CHALLENGE, code="mfa_challenge_invalid")

            used_recovery = False
            if looks_like_recovery_code(cmd.code):
                code_hash = self._secret_hasher.hash(normalise_recovery_code(cmd.code))
                if not factor.consume_recovery_code(code_hash):
                    raise await self._reject(uow, user, state)
                used_recovery = True
            else:
                step = self._totp_step(factor, cmd.code)
                if step is None:
                    raise await self._reject(uow, user, state)
                factor.use_step(step)

            await uow.mfa.upsert(factor)
            user.record_successful_sign_in(self._clock.now())
            await uow.users.save(user)
            if used_recovery:
                uow.audit(
                    _audit(
                        "identity.mfa_recovery_code_used",
                        user,
                        user.id,
                        remaining=len(factor.recovery_code_hashes),
                    )
                )
            issued, _ = await self._issuer.issue(
                uow, user, client=cmd.client, tenant_config_version=None
            )
            uow.audit(
                sign_in_audit.signed_in(
                    user, method="password+recovery_code" if used_recovery else "password+totp"
                )
            )
            await uow.commit()
        await self._challenges.close(state)
        return issued


# ---- enrolment ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MfaSetup:
    secret: str
    otpauth_uri: str


class StartMfaEnrolment(MfaUseCase):
    """Create (or replace) an unconfirmed factor and return the secret for the app to scan."""

    async def __call__(self, challenge_token: str) -> MfaSetup:
        state = await self._challenges.load(challenge_token, ChallengePurpose.ENROL)
        secret = self._secrets.random_bytes(SECRET_BYTES)
        async with self._uow_factory(Scope.platform_wide()) as uow:
            user = await self._user(uow, state.user_id)
            existing = await uow.mfa.get(user.id)
            if user.mfa_enabled or (existing is not None and existing.confirmed):
                raise Conflict("Two-step verification is already set up.", code="mfa_enabled")
            factor = TotpFactor.pending(
                user_id=user.id,
                secret_ciphertext=self._box.encrypt(secret),
                now=self._clock.now(),
            )
            await uow.mfa.upsert(factor)
            # audit: not needed. An unconfirmed factor does nothing until confirmed (audited).
            await uow.commit()
        return MfaSetup(
            secret=totp.encode_secret(secret),
            otpauth_uri=totp.provisioning_uri(
                secret, account=user.email.value, issuer=self._totp_issuer
            ),
        )


@dataclass(frozen=True, slots=True)
class ConfirmMfaCommand:
    challenge_token: str
    code: str
    client: ClientInfo


@dataclass(frozen=True, slots=True)
class MfaEnrolled:
    session: IssuedSession
    recovery_codes: list[str]


class ConfirmMfaEnrolment(MfaUseCase):
    """Prove the app is set up with a first code; turn MFA on and sign in."""

    async def __call__(self, cmd: ConfirmMfaCommand) -> MfaEnrolled:
        await self._limiter.hit(f"mfa:ip:{cmd.client.ip_address}", limit=30, window_seconds=300)
        state = await self._challenges.load(cmd.challenge_token, ChallengePurpose.ENROL)
        async with self._uow_factory(Scope.platform_wide()) as uow:
            user = await self._user(uow, state.user_id)
            factor = await uow.mfa.get(user.id)
            if factor is None:
                raise InvalidStateTransition(
                    "Start setting up two-step verification first.", code="mfa_not_started"
                )
            step = self._totp_step(factor, cmd.code)
            if step is None:
                raise await self._reject(uow, user, state)
            shown, hashes = self._new_recovery_codes()
            factor.confirm(step=step, now=self._clock.now(), recovery_code_hashes=hashes)
            await uow.mfa.upsert(factor)
            user.enable_mfa()
            user.record_successful_sign_in(self._clock.now())
            await uow.users.save(user)
            uow.audit(_audit("identity.mfa_enrolled", user, user.id, method="totp"))
            uow.audit(sign_in_audit.signed_in(user, method="password+totp"))
            issued, _ = await self._issuer.issue(
                uow, user, client=cmd.client, tenant_config_version=None
            )
            await uow.commit()
        await self._challenges.close(state)
        return MfaEnrolled(session=issued, recovery_codes=shown)


# ---- signed-in management ----------------------------------------------------------------


class RegenerateRecoveryCodes(MfaUseCase):
    """Replace every recovery code. Needs a current authenticator code, not just a session."""

    async def __call__(self, actor: Principal, code: str) -> list[str]:
        actor.require_realm(Realm.PLATFORM)
        await self._limiter.hit(f"mfa:user:{actor.user_id}", limit=10, window_seconds=300)
        async with self._uow_factory(Scope.platform_wide()) as uow:
            user = await self._user(uow, actor.user_id)
            factor = await uow.mfa.get(user.id)
            if factor is None or not factor.confirmed:
                raise InvalidStateTransition("Two-step verification is not set up.")
            step = self._totp_step(factor, code)
            if step is None:
                raise await self._reject(uow, user, None)
            factor.use_step(step)
            shown, hashes = self._new_recovery_codes()
            factor.replace_recovery_codes(hashes)
            await uow.mfa.upsert(factor)
            uow.audit(_audit("identity.mfa_recovery_codes_regenerated", user, user.id))
            await uow.commit()
        return shown


class FindPlatformUserId:
    """Operator lookup for the CLI. Not exposed over HTTP."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def __call__(self, email: str) -> UUID:
        async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
            user = await uow.users.get_platform_user_by_email(EmailAddress(email))
        if user is None:
            raise NotFound("No console user has that email.")
        return user.id


class ResetPlatformUserMfa:
    """Break-glass for a lost phone. Signs the person out everywhere; they enrol again next time.

    Staff can't reset their own factor this way, so a stolen session can't remove MFA.
    ``actor=None`` is the operator CLI, which runs with database access on the server.
    """

    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def __call__(self, actor: Principal | None, user_id: UUID) -> None:
        if actor is not None:
            actor.require_realm(Realm.PLATFORM)
            actor.require("platform_users:write")
            if actor.user_id == user_id:
                raise PermissionDenied("Ask another administrator to reset your sign-in.")
        now = self._clock.now()
        async with self._uow_factory(Scope.platform_wide()) as uow:
            user = await uow.users.get(user_id)
            if user is None or user.realm is not Realm.PLATFORM:
                raise NotFound("User not found.")
            await uow.mfa.delete(user.id)
            user.disable_mfa()
            await uow.users.save(user)
            revoked = await uow.sessions.revoke_all_for_user(user.id, now)
            uow.audit(
                _audit(
                    "identity.mfa_reset",
                    user,
                    actor.user_id if actor else None,
                    sessions_revoked=revoked,
                    via="cli" if actor is None else "console",
                )
            )
            await uow.commit()
