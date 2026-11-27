"""Ports: the interfaces use cases depend on. Infrastructure provides the implementations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from masms.domain.academics.repositories import AcademicsRepository
from masms.domain.identity.repositories import (
    InviteRepository,
    MfaRepository,
    RefreshSessionRepository,
    UserRepository,
)
from masms.domain.identity.roles import Realm
from masms.domain.platform.repositories import FeatureFlagRepository, PlanRepository
from masms.domain.shared.entity import AggregateRoot
from masms.domain.shared.events import DomainEvent
from masms.domain.tenancy.repositories import EmailVerificationRepository, TenantRepository


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
    def academics(self) -> AcademicsRepository: ...

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
