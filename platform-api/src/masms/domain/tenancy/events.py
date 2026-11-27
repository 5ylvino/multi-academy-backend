"""Tenancy domain events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from masms.domain.shared.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class TenantRegistered(DomainEvent):
    name: ClassVar[str] = "tenancy.tenant_registered"
    slug: str
    school_name: str


@dataclass(frozen=True, kw_only=True)
class TenantStatusChanged(DomainEvent):
    name: ClassVar[str] = "tenancy.tenant_status_changed"
    previous: str
    current: str
    reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class VerificationCodeIssued(DomainEvent):
    name: ClassVar[str] = "tenancy.verification_code_issued"
    #: Cleared from the outbox row once the email has been handed to the provider.
    sensitive_fields: ClassVar[frozenset[str]] = frozenset({"code"})
    email: str
    school_name: str
    code: str
