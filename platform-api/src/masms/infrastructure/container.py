"""Composition root: builds adapters once per process and wires them into use cases.

Presentation code depends on this module only, never on concrete adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from masms.domain.identity.roles import Realm
from masms.domain.identity.user import LoginPolicy
from masms.infrastructure.cache.redis_cache import RedisCache, RedisRateLimiter, create_redis
from masms.infrastructure.clock import SystemClock
from masms.infrastructure.config.settings import Environment, Settings
from masms.infrastructure.messaging.outbox_relay import Handler, OutboxRelay
from masms.infrastructure.persistence.sqlalchemy.engine import create_engine, create_session_factory
from masms.infrastructure.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWorkFactory
from masms.infrastructure.providers.email.senders import LogEmailSender, SmtpEmailSender
from masms.infrastructure.security.passwords import Argon2PasswordHasher
from masms.infrastructure.security.secrets import CryptoSecretGenerator, HmacSecretHasher
from masms.infrastructure.security.tokens import JwtTokenService, generate_private_key_pem
from masms.use_cases.identity.create_platform_user import CreatePlatformUser
from masms.use_cases.identity.get_current_user import GetCurrentUser
from masms.use_cases.identity.refresh_session import RefreshSessionUseCase
from masms.use_cases.identity.session_issuer import SessionIssuer
from masms.use_cases.identity.sign_in import SignIn
from masms.use_cases.identity.sign_out import SignOut
from masms.use_cases.platform.feature_flags import (
    ListFlags,
    ResolveTenantFeatures,
    SetFlagOverride,
    UpsertFlag,
)
from masms.use_cases.platform.plans import ListPlans, UpsertPlan
from masms.use_cases.shared.ports import AccessClaims, EmailSender
from masms.use_cases.tenancy.manage_tenants import (
    ChangeTenantStatus,
    GetTenant,
    GetTenantRuntime,
    ListTenants,
)
from masms.use_cases.tenancy.notifications import SendVerificationCodeEmail
from masms.use_cases.tenancy.register_school import RegisterSchool
from masms.use_cases.tenancy.school_bootstrap import GetSchoolBootstrap
from masms.use_cases.tenancy.verify_school_email import ResendVerification, VerifySchoolEmail

_log = structlog.get_logger(__name__)


@dataclass
class UseCases:
    school_sign_in: SignIn
    platform_sign_in: SignIn
    school_refresh: RefreshSessionUseCase
    platform_refresh: RefreshSessionUseCase
    sign_out: SignOut
    current_user: GetCurrentUser
    create_platform_user: CreatePlatformUser
    register_school: RegisterSchool
    verify_school_email: VerifySchoolEmail
    resend_verification: ResendVerification
    school_bootstrap: GetSchoolBootstrap
    tenant_runtime: GetTenantRuntime
    list_tenants: ListTenants
    get_tenant: GetTenant
    change_tenant_status: ChangeTenantStatus
    list_flags: ListFlags
    upsert_flag: UpsertFlag
    set_flag_override: SetFlagOverride
    tenant_features: ResolveTenantFeatures
    list_plans: ListPlans
    upsert_plan: UpsertPlan


@dataclass
class Container:
    settings: Settings
    use_cases: UseCases
    tokens: JwtTokenService
    outbox: OutboxRelay
    session_factory: async_sessionmaker[AsyncSession]
    _engines: list[AsyncEngine] = field(default_factory=list)
    _redis: object | None = None

    def decode_access(self, token: str, realm: Realm) -> AccessClaims:
        return self.tokens.decode_access(token, realm=realm)

    async def check_ready(self) -> dict[str, str]:
        """Readiness: every dependency the API needs to serve traffic."""
        status: dict[str, str] = {}
        try:
            async with self._engines[0].connect() as conn:
                await conn.execute(text("SELECT 1"))
            status["postgres"] = "ok"
        except Exception as exc:
            status["postgres"] = f"error: {type(exc).__name__}"
        try:
            await self._redis.ping()  # type: ignore[attr-defined]
            status["redis"] = "ok"
        except Exception as exc:
            status["redis"] = f"error: {type(exc).__name__}"
        return status

    async def aclose(self) -> None:
        for engine in self._engines:
            await engine.dispose()
        if self._redis is not None:
            await self._redis.aclose()  # type: ignore[attr-defined]


def _email_sender(settings: Settings) -> EmailSender:
    if settings.email_provider == "smtp":
        if not settings.smtp_host:
            raise ValueError("MASMS_SMTP_HOST is required when MASMS_EMAIL_PROVIDER=smtp")
        return SmtpEmailSender(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
            start_tls=settings.smtp_starttls,
            from_address=settings.email_from_address,
            from_name=settings.email_from_name,
        )
    return LogEmailSender()


def build_container(settings: Settings, *, role: str = "api") -> Container:
    primary = create_engine(
        settings.database_url.get_secret_value(), settings, application_name=f"masms-{role}"
    )
    engines = [primary]
    replica_factory = None
    if settings.database_read_url is not None:
        replica = create_engine(
            settings.database_read_url.get_secret_value(),
            settings,
            application_name=f"masms-{role}-ro",
        )
        engines.append(replica)
        replica_factory = create_session_factory(replica)
    session_factory = create_session_factory(primary)
    uow = SqlAlchemyUnitOfWorkFactory(session_factory, replica_factory)

    redis = create_redis(settings.redis_url.get_secret_value())
    cache = RedisCache(redis)
    limiter = RedisRateLimiter(redis)
    clock = SystemClock()
    hasher = Argon2PasswordHasher(
        **(
            {"time_cost": 1, "memory_cost": 8, "parallelism": 1}
            if settings.environment is Environment.TEST
            else {}
        )
    )
    secret_hasher = HmacSecretHasher(settings.secret_pepper.get_secret_value().encode())
    secrets = CryptoSecretGenerator()

    if settings.jwt_private_key_pem is not None:
        private_pem = settings.jwt_private_key_pem.get_secret_value()
    else:
        # Non-production only (settings validation refuses this in production): a key per process.
        _log.warning(
            "tokens.ephemeral_key",
            detail="Set MASMS_JWT_PRIVATE_KEY_PEM to keep sessions across restarts",
        )
        private_pem = generate_private_key_pem()
    tokens = JwtTokenService(
        private_key_pem=private_pem,
        key_id=settings.jwt_key_id,
        issuer=settings.jwt_issuer,
        access_ttl_seconds=settings.access_token_ttl_seconds,
        previous_public_keys=settings.jwt_previous_public_keys,
    )

    def issuer(ttl: timedelta) -> SessionIssuer:
        return SessionIssuer(
            tokens=tokens,
            secrets=secrets,
            secret_hasher=secret_hasher,
            clock=clock,
            refresh_ttl=ttl,
        )

    school_issuer = issuer(timedelta(days=settings.refresh_token_ttl_days))
    platform_issuer = issuer(timedelta(hours=settings.platform_refresh_token_ttl_hours))
    policy = LoginPolicy()
    runtime = GetTenantRuntime(uow_factory=uow, cache=cache)
    features = ResolveTenantFeatures(uow_factory=uow, cache=cache)

    use_cases = UseCases(
        school_sign_in=SignIn(
            uow_factory=uow,
            hasher=hasher,
            issuer=school_issuer,
            limiter=limiter,
            clock=clock,
            policy=policy,
        ),
        platform_sign_in=SignIn(
            uow_factory=uow,
            hasher=hasher,
            issuer=platform_issuer,
            limiter=limiter,
            clock=clock,
            policy=policy,
        ),
        school_refresh=RefreshSessionUseCase(
            uow_factory=uow, secret_hasher=secret_hasher, issuer=school_issuer, clock=clock
        ),
        platform_refresh=RefreshSessionUseCase(
            uow_factory=uow, secret_hasher=secret_hasher, issuer=platform_issuer, clock=clock
        ),
        sign_out=SignOut(uow_factory=uow, secret_hasher=secret_hasher, clock=clock),
        current_user=GetCurrentUser(uow_factory=uow),
        create_platform_user=CreatePlatformUser(uow_factory=uow, hasher=hasher, clock=clock),
        register_school=RegisterSchool(
            uow_factory=uow,
            hasher=hasher,
            secret_hasher=secret_hasher,
            secrets=secrets,
            limiter=limiter,
            clock=clock,
            default_plan=settings.default_plan_code,
        ),
        verify_school_email=VerifySchoolEmail(
            uow_factory=uow,
            secret_hasher=secret_hasher,
            issuer=school_issuer,
            limiter=limiter,
            clock=clock,
            trial_length=timedelta(days=settings.trial_days),
        ),
        resend_verification=ResendVerification(
            uow_factory=uow,
            secret_hasher=secret_hasher,
            secrets=secrets,
            limiter=limiter,
            clock=clock,
        ),
        school_bootstrap=GetSchoolBootstrap(
            uow_factory=uow, cache=cache, runtime=runtime, features=features
        ),
        tenant_runtime=runtime,
        list_tenants=ListTenants(uow_factory=uow),
        get_tenant=GetTenant(uow_factory=uow),
        change_tenant_status=ChangeTenantStatus(uow_factory=uow, cache=cache, clock=clock),
        list_flags=ListFlags(uow_factory=uow),
        upsert_flag=UpsertFlag(uow_factory=uow, cache=cache, clock=clock),
        set_flag_override=SetFlagOverride(uow_factory=uow, cache=cache),
        tenant_features=features,
        list_plans=ListPlans(uow_factory=uow),
        upsert_plan=UpsertPlan(uow_factory=uow, cache=cache),
    )

    email = _email_sender(settings)
    handlers: dict[str, list[Handler]] = {
        "tenancy.verification_code_issued": [SendVerificationCodeEmail(email=email)],
    }
    outbox = OutboxRelay(session_factory, handlers)
    return Container(
        settings=settings,
        use_cases=use_cases,
        tokens=tokens,
        outbox=outbox,
        session_factory=session_factory,
        _engines=engines,
        _redis=redis,
    )
