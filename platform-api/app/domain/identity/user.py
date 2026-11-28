"""User accounts for both realms, with login lockout rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from app.domain.identity.events import UserCreated, UserLockedOut
from app.domain.identity.permissions import PermissionSet
from app.domain.identity.roles import Realm
from app.domain.shared.entity import AggregateRoot
from app.domain.shared.errors import AccountLocked, AuthenticationFailed, InvalidStateTransition
from app.domain.shared.ids import new_id
from app.domain.shared.value_objects import EmailAddress


class UserStatus(StrEnum):
    PENDING = "pending"  # created, waiting for email verification or first password
    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class LoginPolicy:
    max_failed_attempts: int = 5
    lockout: timedelta = timedelta(minutes=15)


@dataclass(kw_only=True, eq=False)
class UserAccount(AggregateRoot):
    realm: Realm
    tenant_id: UUID | None
    email: EmailAddress
    full_name: str
    password_hash: str
    roles: frozenset[str]
    capabilities: frozenset[str] = frozenset()
    primary_owner: bool = False
    status: UserStatus = UserStatus.ACTIVE
    failed_login_count: int = 0
    locked_until: datetime | None = None
    last_login_at: datetime | None = None
    mfa_enabled: bool = False
    created_at: datetime
    version: int = field(default=1)

    def __post_init__(self) -> None:
        if self.realm is Realm.SCHOOL and self.tenant_id is None:
            raise ValueError("School users must belong to a tenant.")
        if self.realm is Realm.PLATFORM and self.tenant_id is not None:
            raise ValueError("Platform users must not belong to a tenant.")

    @classmethod
    def create(
        cls,
        *,
        realm: Realm,
        tenant_id: UUID | None,
        email: EmailAddress,
        full_name: str,
        password_hash: str,
        roles: frozenset[str],
        now: datetime,
        capabilities: frozenset[str] = frozenset(),
        primary_owner: bool = False,
        status: UserStatus = UserStatus.ACTIVE,
    ) -> UserAccount:
        user = cls(
            id=new_id(),
            realm=realm,
            tenant_id=tenant_id,
            email=email,
            full_name=full_name.strip(),
            password_hash=password_hash,
            roles=roles,
            capabilities=capabilities,
            primary_owner=primary_owner,
            status=status,
            created_at=now,
        )
        user.record(
            UserCreated(tenant_id=tenant_id, user_id=user.id, realm=realm.value, email=email.value)
        )
        return user

    @property
    def permissions(self) -> PermissionSet:
        return PermissionSet.for_realm(
            self.realm, self.roles, self.capabilities, primary_owner=self.primary_owner
        )

    def is_locked(self, now: datetime) -> bool:
        return self.locked_until is not None and self.locked_until > now

    def ensure_can_sign_in(self, now: datetime) -> None:
        if self.is_locked(now):
            raise AccountLocked(
                "Too many failed sign-in attempts. Try again later.",
                details={
                    "locked_until": self.locked_until.isoformat() if self.locked_until else None
                },
            )
        if self.status is not UserStatus.ACTIVE:
            # Same message as a bad password so the response does not reveal account state.
            raise AuthenticationFailed("Email or password is incorrect.")

    def record_failed_sign_in(self, now: datetime, policy: LoginPolicy) -> None:
        self.failed_login_count += 1
        if self.failed_login_count >= policy.max_failed_attempts:
            self.locked_until = now + policy.lockout
            self.failed_login_count = 0
            self.record(UserLockedOut(tenant_id=self.tenant_id, user_id=self.id))

    def record_successful_sign_in(self, now: datetime) -> None:
        self.failed_login_count = 0
        self.locked_until = None
        self.last_login_at = now

    def activate(self) -> None:
        if self.status is UserStatus.DISABLED:
            raise InvalidStateTransition("A disabled account must be re-enabled by an admin.")
        self.status = UserStatus.ACTIVE

    def disable(self) -> None:
        self.status = UserStatus.DISABLED

    def enable(self) -> None:
        self.status = UserStatus.ACTIVE

    def enable_mfa(self) -> None:
        self.mfa_enabled = True

    def disable_mfa(self) -> None:
        self.mfa_enabled = False

    def change_password_hash(self, new_hash: str) -> None:
        self.password_hash = new_hash
