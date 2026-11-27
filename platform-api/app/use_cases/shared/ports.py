"""Ports: the interfaces use cases depend on. Infrastructure provides the implementations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from app.domain.academics.repositories import AcademicsRepository
from app.domain.attendance.repositories import AttendanceRepository
from app.domain.communication.repositories import CommunicationRepository
from app.domain.finance.repositories import (
    FinanceRepository,
    OnlinePaymentRepository,
    SpendingRepository,
)
from app.domain.identity.repositories import (
    GuardianRepository,
    InviteRepository,
    MfaRepository,
    RefreshSessionRepository,
    UserRepository,
)
from app.domain.identity.roles import Realm
from app.domain.inventory.repositories import InventoryRepository
from app.domain.payroll.repositories import PayrollRepository
from app.domain.platform.repositories import FeatureFlagRepository, PlanRepository
from app.domain.safeguarding.repositories import SafeguardingRepository
from app.domain.shared.entity import AggregateRoot
from app.domain.shared.events import DomainEvent
from app.domain.subscriptions.repositories import SubscriptionRepository
from app.domain.tenancy.repositories import EmailVerificationRepository, TenantRepository


class Clock(Protocol):
    def now(self) -> datetime: ...


class PasswordHasher(Protocol):
    async def hash(self, password: str) -> str: ...

    async def verify(self, password_hash: str, password: str) -> bool: ...

    def needs_rehash(self, password_hash: str) -> bool: ...

    async def burn_time(self) -> None:
        """Spend the same time as a real verify, so unknown emails can't be detected by timing."""
        ...


class SecretHasher(Protocol):
    """Keyed hashing (HMAC) for high-entropy secrets: refresh tokens and verification codes."""

    def hash(self, secret: str) -> str: ...

    def matches(self, secret: str, secret_hash: str) -> bool: ...


class SecretGenerator(Protocol):
    def token(self) -> str:
        """An opaque, URL-safe token with at least 256 bits of entropy."""
        ...

    def numeric_code(self, digits: int) -> str: ...

    def random_bytes(self, length: int) -> bytes: ...

    def code_from_alphabet(self, alphabet: str, length: int) -> str: ...


class ObjectStore(Protocol):
    """Private file storage (receipts, photos). Keys are chosen by the server, never the client."""

    async def put(self, key: str, data: bytes, *, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes:
        """Raises ``NotFound`` when the object does not exist."""
        ...

    async def delete(self, key: str) -> None: ...


class SchoolTime(Protocol):
    """The current time in a school's own time zone."""

    async def now_local(self, tenant_id: UUID) -> datetime: ...


class SecretBox(Protocol):
    """Authenticated encryption for secrets we must read back (e.g. authenticator seeds)."""

    def encrypt(self, plaintext: bytes) -> str: ...

    def decrypt(self, ciphertext: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class AccessClaims:
    realm: Realm
    user_id: UUID
    tenant_id: UUID | None
    roles: frozenset[str]
    capabilities: frozenset[str]
    primary_owner: bool
    session_family: UUID
    tenant_config_version: int | None
    token_id: str
    expires_at: datetime


class TokenService(Protocol):
    def issue_access(self, claims: AccessClaims) -> str: ...

    def decode_access(self, token: str, *, realm: Realm) -> AccessClaims: ...

    @property
    def access_ttl_seconds(self) -> int: ...


class Cache(Protocol):
    async def get_json(self, key: str) -> Any | None: ...

    async def set_json(self, key: str, value: Any, ttl_seconds: int) -> None: ...

    async def delete(self, *keys: str) -> None: ...

    async def get_version(self, name: str) -> int: ...

    async def bump_version(self, name: str) -> int: ...


class RateLimiter(Protocol):
    async def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
        """Count one hit; raise ``RateLimited`` when the limit for the window is exceeded."""
        ...


@dataclass(frozen=True, slots=True)
class AuditEntry:
    action: str
    actor_id: UUID | None
    actor_realm: Realm | None
    tenant_id: UUID | None
    target_type: str
    target_id: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AuditFilter:
    """What to search the audit trail for. ``tenant_id`` None with ``platform_only`` means events
    with no school (owner-console work); None without it means every school (platform scope)."""

    tenant_id: UUID | None = None
    platform_only: bool = False
    actor_id: UUID | None = None
    action_prefix: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    #: Keyset cursor: return events strictly older than (created_at, id).
    before: tuple[datetime, UUID] | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class AuditRecord:
    id: UUID
    created_at: datetime
    tenant_id: UUID | None
    actor_id: UUID | None
    actor_realm: str | None
    actor_name: str | None
    actor_email: str | None
    action: str
    target_type: str
    target_id: str
    metadata: Mapping[str, Any]
    request_id: str | None
    ip_address: str | None
    user_agent: str | None


class AuditLogRepository(Protocol):
    async def search(self, query: AuditFilter) -> list[AuditRecord]:
        """Newest first. RLS limits a school scope to its own events."""
        ...


# ---- payment providers ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GatewayCredentials:
    public_key: str
    secret_key: str


@dataclass(frozen=True, slots=True)
class CheckoutRequest:
    reference: str
    amount_minor: int
    currency: str
    email: str
    callback_url: str
    metadata: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    authorization_url: str


@dataclass(frozen=True, slots=True)
class GatewayTransaction:
    """What the provider's API says about a payment. ``status`` is one of ``success``,
    ``failed``, ``abandoned`` or ``pending``."""

    reference: str
    status: str
    amount_minor: int
    currency: str
    transaction_id: str | None
    channel: str | None
    gateway_response: str | None


@dataclass(frozen=True, slots=True)
class GatewayNotice:
    """A verified webhook. Only says which payment to check; never trusted for amounts."""

    event: str
    reference: str | None


class PaymentGateway(Protocol):
    """One payment provider. Adding a provider means adding one adapter; use cases don't change.

    Methods raise ``ProviderUnavailable`` when the provider can't be reached, and
    ``ValidationFailed`` for input the provider refuses (such as a wrong key).
    """

    @property
    def provider(self) -> str: ...

    def key_mode(self, secret_key: str) -> str:
        """``live`` or ``test`` from the key's format; raises ValidationFailed if malformed."""
        ...

    async def check_credentials(self, credentials: GatewayCredentials) -> None: ...

    async def initialize(
        self, credentials: GatewayCredentials, request: CheckoutRequest
    ) -> CheckoutSession: ...

    async def verify(self, credentials: GatewayCredentials, reference: str) -> GatewayTransaction:
        """Ask the provider for the real state of a payment."""
        ...

    def read_webhook(
        self, credentials: GatewayCredentials, headers: Mapping[str, str], body: bytes
    ) -> GatewayNotice:
        """Check the signature and parse the event. Raises AuthenticationFailed if forged."""
        ...


class PaymentGateways(Protocol):
    def get(self, provider: str) -> PaymentGateway:
        """Raises ValidationFailed for a provider that isn't available."""
        ...

    def providers(self) -> list[str]: ...


class UnitOfWork(Protocol):
    """One database transaction.

    School data is protected by row-level security: until ``enter_tenant`` is called, tenant-owned
    tables return no rows. Events recorded on tracked aggregates are written to the outbox in the
    same transaction as the data, so they are never lost or sent for rolled-back changes.
    """

    @property
    def users(self) -> UserRepository: ...

    @property
    def sessions(self) -> RefreshSessionRepository: ...

    @property
    def tenants(self) -> TenantRepository: ...

    @property
    def verifications(self) -> EmailVerificationRepository: ...

    @property
    def flags(self) -> FeatureFlagRepository: ...

    @property
    def plans(self) -> PlanRepository: ...

    @property
    def invites(self) -> InviteRepository: ...

    @property
    def mfa(self) -> MfaRepository: ...

    @property
    def guardians(self) -> GuardianRepository: ...

    @property
    def attendance(self) -> AttendanceRepository: ...

    @property
    def academics(self) -> AcademicsRepository: ...

    @property
    def finance(self) -> FinanceRepository: ...

    @property
    def online_payments(self) -> OnlinePaymentRepository: ...

    @property
    def subscriptions(self) -> SubscriptionRepository: ...

    @property
    def spending(self) -> SpendingRepository: ...

    @property
    def inventory(self) -> InventoryRepository: ...

    @property
    def payroll(self) -> PayrollRepository: ...

    @property
    def communication(self) -> CommunicationRepository: ...

    @property
    def safeguarding(self) -> SafeguardingRepository: ...

    @property
    def audit_log(self) -> AuditLogRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def enter_tenant(self, tenant_id: UUID) -> None: ...

    def track(self, aggregate: AggregateRoot) -> None: ...

    def publish(self, event: DomainEvent) -> None: ...

    def audit(self, entry: AuditEntry) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Scope:
    """What a transaction may see. ``platform`` bypasses tenant isolation for owner-console work."""

    tenant_id: UUID | None = None
    platform: bool = False

    @classmethod
    def registry(cls) -> Scope:
        """No tenant yet: only registry tables (tenants, sessions, flags) are visible."""
        return cls()

    @classmethod
    def tenant(cls, tenant_id: UUID) -> Scope:
        return cls(tenant_id=tenant_id)

    @classmethod
    def platform_wide(cls) -> Scope:
        return cls(platform=True)


class UnitOfWorkFactory(Protocol):
    def __call__(self, scope: Scope, *, read_only: bool = False) -> UnitOfWork: ...


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str | None = None


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None: ...
