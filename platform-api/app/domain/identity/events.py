"""Identity domain events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from app.domain.shared.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class UserCreated(DomainEvent):
    name: ClassVar[str] = "identity.user_created"
    user_id: UUID
    realm: str
    email: str


@dataclass(frozen=True, kw_only=True)
class UserLockedOut(DomainEvent):
    name: ClassVar[str] = "identity.user_locked_out"
    user_id: UUID


@dataclass(frozen=True, kw_only=True)
class RefreshTokenReuseDetected(DomainEvent):
    name: ClassVar[str] = "identity.refresh_token_reuse_detected"
    user_id: UUID
    family_id: UUID


@dataclass(frozen=True, kw_only=True)
class UserInvited(DomainEvent):
    name: ClassVar[str] = "identity.user_invited"
    sensitive_fields: ClassVar[frozenset[str]] = frozenset({"token"})
    user_id: UUID
    email: str
    full_name: str
    school_name: str
    school_slug: str
    token: str
