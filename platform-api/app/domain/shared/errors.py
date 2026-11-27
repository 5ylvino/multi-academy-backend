"""Domain error hierarchy.

Every error carries a stable machine-readable ``code`` so the presentation layer can map it to
a transport response without inspecting messages.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class DomainError(Exception):
    """Base class for every expected business failure."""

    code: str = "domain_error"

    def __init__(
        self, message: str, *, code: str | None = None, details: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details: dict[str, Any] = dict(details or {})


class ValidationFailed(DomainError):
    code = "validation_failed"


class NotFound(DomainError):
    code = "not_found"


class Conflict(DomainError):
    code = "conflict"


class InvalidStateTransition(DomainError):
    code = "invalid_state_transition"


class AuthenticationFailed(DomainError):
    code = "authentication_failed"


class PermissionDenied(DomainError):
    code = "permission_denied"


class AccountLocked(DomainError):
    code = "account_locked"


class TenantUnavailable(DomainError):
    code = "tenant_unavailable"


class FeatureDisabled(DomainError):
    code = "feature_disabled"


class RateLimited(DomainError):
    code = "rate_limited"


class ProviderUnavailable(DomainError):
    """An outside service (payment provider, email, storage) failed or timed out. Safe to retry."""

    code = "provider_unavailable"
