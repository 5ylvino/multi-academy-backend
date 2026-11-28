"""Composition root: builds adapters once per process and wires them into use cases.

Presentation code depends on this module only, never on concrete adapters.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TypeVar

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.domain.identity.roles import Realm
from app.domain.identity.user import LoginPolicy
from app.infrastructure.cache.redis_cache import RedisCache, RedisRateLimiter, create_redis
from app.infrastructure.clock import SystemClock
from app.infrastructure.config.settings import Environment, Settings
from app.infrastructure.messaging.outbox_relay import Handler, OutboxRelay
from app.infrastructure.persistence.sqlalchemy.engine import create_engine, create_session_factory
from app.infrastructure.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWorkFactory
from app.infrastructure.providers.email.senders import LogEmailSender, SmtpEmailSender
from app.infrastructure.providers.payments.paystack import PaystackGateway
from app.infrastructure.providers.payments.registry import GatewayRegistry
from app.infrastructure.security.passwords import Argon2PasswordHasher
from app.infrastructure.security.secret_box import AesGcmSecretBox, decode_key
from app.infrastructure.security.secrets import CryptoSecretGenerator, HmacSecretHasher
from app.infrastructure.security.tokens import JwtTokenService, generate_private_key_pem
from app.infrastructure.storage.local import LocalObjectStore
from app.infrastructure.storage.s3 import S3ObjectStore
from app.use_cases.academics.structure import AcademicCalendar, ClassesAndSubjects, Enrolments
from app.use_cases.assessment.gradebook import Gradebook
from app.use_cases.assessment.notifications import SendReportCardEmails
from app.use_cases.assessment.results import Results
from app.use_cases.assessment.setup import AssessmentSetup
from app.use_cases.attendance.registers import StudentRegisters
from app.use_cases.attendance.staff import StaffAttendance
from app.use_cases.audit.trail import AuditTrail
from app.use_cases.communication.alerts import SendUrgentAnnouncement
from app.use_cases.communication.announcements import Announcements
from app.use_cases.communication.conversations import Conversations
from app.use_cases.finance.billing import Billing
from app.use_cases.finance.catalog import FinanceCatalog
from app.use_cases.finance.online import OnlinePayments
from app.use_cases.finance.payments import Payments
from app.use_cases.finance.proofs import PaymentProofs
from app.use_cases.finance.spending import Spending
from app.use_cases.finance.statements import Statements
from app.use_cases.identity.create_platform_user import CreatePlatformUser
from app.use_cases.identity.get_current_user import GetCurrentUser
from app.use_cases.identity.guardians import Guardians
from app.use_cases.identity.manage_school_users import (
    AcceptInvite,
    InviteSchoolUser,
    ListSchoolUsers,
)
from app.use_cases.identity.mfa import (
    ConfirmMfaEnrolment,
    FindPlatformUserId,
    MfaChallenges,
    MfaUseCase,
    RegenerateRecoveryCodes,
    ResetPlatformUserMfa,
    StartMfaEnrolment,
    VerifyMfaSignIn,
)
from app.use_cases.identity.notifications import SendInviteEmail
from app.use_cases.identity.refresh_session import RefreshSessionUseCase
from app.use_cases.identity.session_issuer import SessionIssuer
from app.use_cases.identity.sign_in import SignIn
from app.use_cases.identity.sign_out import SignOut
from app.use_cases.inventory.stock import Inventory
from app.use_cases.operations.clinic import Clinic
from app.use_cases.operations.gate import Gate
from app.use_cases.operations.hostel import Hostels
from app.use_cases.operations.notifications import SendUrgentClinicEmail
from app.use_cases.operations.transport import Transport
from app.use_cases.payroll.notifications import SendPayslipReadyEmails
from app.use_cases.payroll.runs import Payroll
from app.use_cases.platform.feature_flags import (
    ListFlags,
    ResolveTenantFeatures,
    SetFlagOverride,
    UpsertFlag,
)
from app.use_cases.platform.plans import ListPlans, UpsertPlan
from app.use_cases.safeguarding.alerts import SendUrgentConcernAlert
from app.use_cases.safeguarding.cases import Safeguarding
from app.use_cases.shared.ports import (
    AccessClaims,
    EmailSender,
    GatewayCredentials,
    ObjectStore,
)
from app.use_cases.subscriptions.billing import PlatformBilling, PlatformPaymentConfig
from app.use_cases.subscriptions.notifications import SendPlatformInvoiceEmail
from app.use_cases.tenancy.manage_tenants import (
    ChangeTenantStatus,
    GetTenant,
    GetTenantRuntime,
    ListTenants,
)
from app.use_cases.tenancy.notifications import SendVerificationCodeEmail
from app.use_cases.tenancy.register_school import RegisterSchool
from app.use_cases.tenancy.school_bootstrap import GetSchoolBootstrap
from app.use_cases.tenancy.school_time import TenantSchoolTime
from app.use_cases.tenancy.verify_school_email import ResendVerification, VerifySchoolEmail

_log = structlog.get_logger(__name__)
M = TypeVar("M", bound=MfaUseCase)


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
    invite_school_user: InviteSchoolUser
    accept_invite: AcceptInvite
    list_school_users: ListSchoolUsers
    academic_calendar: AcademicCalendar
    classes_and_subjects: ClassesAndSubjects
    enrolments: Enrolments
    verify_mfa: VerifyMfaSignIn
    start_mfa_enrolment: StartMfaEnrolment
    confirm_mfa_enrolment: ConfirmMfaEnrolment
    regenerate_recovery_codes: RegenerateRecoveryCodes
    reset_platform_mfa: ResetPlatformUserMfa
    find_platform_user_id: FindPlatformUserId
    student_registers: StudentRegisters
    staff_attendance: StaffAttendance
    finance_catalog: FinanceCatalog
    billing: Billing
    payments: Payments
    statements: Statements
    payment_proofs: PaymentProofs
    guardians: Guardians
    audit_trail: AuditTrail
    online_payments: OnlinePayments
    platform_billing: PlatformBilling
    spending: Spending
    inventory: Inventory
    payroll: Payroll
    announcements: Announcements
    conversations: Conversations
    safeguarding: Safeguarding
    clinic: Clinic
    gate: Gate
    transport: Transport
    hostels: Hostels
    assessment_setup: AssessmentSetup
    gradebook: Gradebook
    results: Results


@dataclass
class Container:
    settings: Settings
    use_cases: UseCases
    tokens: JwtTokenService
    outbox: OutboxRelay
    session_factory: async_sessionmaker[AsyncSession]
    _engines: list[AsyncEngine] = field(default_factory=list)
    _redis: object | None = None
    _gateways: GatewayRegistry | None = None

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
        if self._gateways is not None:
            await self._gateways.aclose()


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


def _object_store(settings: Settings) -> ObjectStore:
    if settings.object_store == "s3":
        if not (
            settings.object_store_endpoint
            and settings.object_store_access_key_id
            and settings.object_store_secret_access_key
        ):
            raise ValueError("Object storage endpoint and keys are required for s3.")
        return S3ObjectStore(
            endpoint=settings.object_store_endpoint,
            bucket=settings.object_store_bucket,
            region=settings.object_store_region,
            access_key=settings.object_store_access_key_id,
            secret_key=settings.object_store_secret_access_key.get_secret_value(),
        )
    if settings.object_store != "local":
        raise ValueError(f"Unknown object store {settings.object_store!r}.")
    return LocalObjectStore(settings.object_store_path)


def _dev_key(settings: Settings, purpose: bytes) -> bytes:
    pepper = settings.secret_pepper.get_secret_value().encode()
    return hmac.new(pepper, b"masms-dev-key:" + purpose, hashlib.sha256).digest()


def _secret_box(settings: Settings) -> AesGcmSecretBox:
    if settings.data_keys:
        keys = {kid: decode_key(v.get_secret_value()) for kid, v in settings.data_keys.items()}
        return AesGcmSecretBox(current_key_id=settings.data_key_id, keys=keys)
    _log.warning("secrets.derived_dev_data_key", detail="Set MASMS_DATA_KEYS")
    return AesGcmSecretBox(current_key_id="dev", keys={"dev": _dev_key(settings, b"data-key")})


def build_container(
    settings: Settings, *, role: str = "api", payment_gateways: GatewayRegistry | None = None
) -> Container:
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
        # Non-production only (settings validation refuses this in production). Derived from the
        # pepper so every worker process of a dev deployment agrees on the key.
        _log.warning("tokens.derived_dev_key", detail="Set MASMS_JWT_PRIVATE_KEY_PEM")
        private_pem = generate_private_key_pem(_dev_key(settings, b"jwt-signing"))
    secret_box = _secret_box(settings)
    object_store = _object_store(settings)
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
    school_time = TenantSchoolTime(runtime=runtime, clock=clock)
    gateways = payment_gateways or GatewayRegistry(
        [PaystackGateway(base_url=settings.paystack_base_url)]
    )
    platform_key = settings.platform_paystack_secret_key
    platform_billing = PlatformBilling(
        uow_factory=uow,
        gateways=gateways,
        payment=PlatformPaymentConfig(
            provider="paystack",
            credentials=(
                GatewayCredentials(
                    public_key=settings.platform_paystack_public_key or "",
                    secret_key=platform_key.get_secret_value(),
                )
                if platform_key is not None
                else None
            ),
            pay_page_url=settings.effective_platform_pay_page_url,
            bank_transfer_details=settings.platform_bank_transfer_details,
        ),
        secret_hasher=secret_hasher,
        cache=cache,
        clock=clock,
        limiter=limiter,
    )
    challenges = MfaChallenges(cache=cache, secrets=secrets, secret_hasher=secret_hasher)

    def mfa_use_case(cls: type[M]) -> M:
        return cls(
            uow_factory=uow,
            challenges=challenges,
            box=secret_box,
            secrets=secrets,
            secret_hasher=secret_hasher,
            issuer=platform_issuer,
            limiter=limiter,
            clock=clock,
            policy=policy,
            totp_issuer=settings.mfa_issuer,
        )

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
            mfa=challenges if settings.platform_mfa_required else None,
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
        invite_school_user=InviteSchoolUser(
            uow_factory=uow, secret_hasher=secret_hasher, secrets=secrets, clock=clock
        ),
        accept_invite=AcceptInvite(
            uow_factory=uow,
            hasher=hasher,
            secret_hasher=secret_hasher,
            issuer=school_issuer,
            limiter=limiter,
            clock=clock,
        ),
        list_school_users=ListSchoolUsers(uow_factory=uow),
        academic_calendar=AcademicCalendar(uow_factory=uow, clock=clock),
        classes_and_subjects=ClassesAndSubjects(uow_factory=uow),
        enrolments=Enrolments(uow_factory=uow, clock=clock),
        verify_mfa=mfa_use_case(VerifyMfaSignIn),
        start_mfa_enrolment=mfa_use_case(StartMfaEnrolment),
        confirm_mfa_enrolment=mfa_use_case(ConfirmMfaEnrolment),
        regenerate_recovery_codes=mfa_use_case(RegenerateRecoveryCodes),
        reset_platform_mfa=ResetPlatformUserMfa(uow_factory=uow, clock=clock),
        find_platform_user_id=FindPlatformUserId(uow_factory=uow),
        student_registers=StudentRegisters(uow_factory=uow, clock=clock, school_time=school_time),
        staff_attendance=StaffAttendance(
            uow_factory=uow,
            clock=clock,
            school_time=school_time,
            box=secret_box,
            secrets=secrets,
            limiter=limiter,
        ),
        finance_catalog=FinanceCatalog(uow_factory=uow, clock=clock),
        billing=Billing(uow_factory=uow, clock=clock, school_time=school_time),
        payments=Payments(uow_factory=uow, clock=clock, school_time=school_time),
        statements=Statements(uow_factory=uow),
        payment_proofs=PaymentProofs(
            uow_factory=uow,
            clock=clock,
            school_time=school_time,
            store=object_store,
            limiter=limiter,
        ),
        guardians=Guardians(uow_factory=uow, clock=clock),
        audit_trail=AuditTrail(uow_factory=uow, clock=clock),
        online_payments=OnlinePayments(
            uow_factory=uow,
            gateways=gateways,
            box=secret_box,
            clock=clock,
            school_time=school_time,
            limiter=limiter,
            callback_url=settings.effective_payment_callback_url,
            public_api_url=settings.public_api_url,
            allow_test_keys=settings.payments_allow_test_keys,
        ),
        platform_billing=platform_billing,
        inventory=Inventory(uow_factory=uow, clock=clock),
        payroll=Payroll(uow_factory=uow, clock=clock, school_time=school_time, box=secret_box),
        announcements=Announcements(uow_factory=uow, clock=clock),
        conversations=Conversations(uow_factory=uow, clock=clock, limiter=limiter),
        safeguarding=Safeguarding(uow_factory=uow, clock=clock, box=secret_box),
        clinic=Clinic(uow_factory=uow, clock=clock, box=secret_box),
        gate=Gate(uow_factory=uow, clock=clock, school_time=school_time),
        transport=Transport(uow_factory=uow, clock=clock, school_time=school_time),
        hostels=Hostels(uow_factory=uow, clock=clock),
        assessment_setup=AssessmentSetup(uow_factory=uow),
        gradebook=Gradebook(uow_factory=uow, clock=clock, school_time=school_time),
        results=Results(uow_factory=uow, clock=clock),
        spending=Spending(
            uow_factory=uow, clock=clock, school_time=school_time, store=object_store
        ),
    )

    email = _email_sender(settings)
    handlers: dict[str, list[Handler]] = {
        "tenancy.verification_code_issued": [SendVerificationCodeEmail(email=email)],
        "identity.user_invited": [
            SendInviteEmail(email=email, school_app_url=settings.school_app_url)
        ],
    }
    invoice_email = SendPlatformInvoiceEmail(
        email=email,
        uow_factory=uow,
        pay_link=platform_billing.pay_link,
        bank_transfer_details=settings.platform_bank_transfer_details,
    )
    for name in (
        "subscriptions.invoice_issued",
        "subscriptions.invoice_overdue",
        "subscriptions.invoice_paid",
    ):
        handlers[name] = [invoice_email]
    handlers["payroll.run_paid"] = [
        SendPayslipReadyEmails(email=email, uow_factory=uow, school_app_url=settings.school_app_url)
    ]
    handlers["communication.urgent_announcement"] = [
        SendUrgentAnnouncement(email=email, uow_factory=uow, school_app_url=settings.school_app_url)
    ]
    handlers["safeguarding.urgent_concern"] = [
        SendUrgentConcernAlert(email=email, uow_factory=uow, school_app_url=settings.school_app_url)
    ]
    handlers["operations.clinic_visit_closed"] = [
        SendUrgentClinicEmail(email=email, uow_factory=uow, school_app_url=settings.school_app_url)
    ]
    handlers["assessment.results_released"] = [
        SendReportCardEmails(email=email, uow_factory=uow, school_app_url=settings.school_app_url)
    ]
    outbox = OutboxRelay(session_factory, handlers)
    return Container(
        settings=settings,
        use_cases=use_cases,
        tokens=tokens,
        outbox=outbox,
        session_factory=session_factory,
        _engines=engines,
        _redis=redis,
        _gateways=gateways,
    )
