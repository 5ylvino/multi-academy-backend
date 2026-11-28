"""Audit entries for sign-in outcomes, shared by password and two-step sign-in."""

from __future__ import annotations

from app.domain.identity.user import UserAccount
from app.use_cases.shared import audit
from app.use_cases.shared.ports import AuditEntry


def signed_in(user: UserAccount, *, method: str) -> AuditEntry:
    return audit.system(
        "identity.signed_in",
        target_type="user",
        target_id=user.id,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        actor_realm=user.realm,
        method=method,
    )


def sign_in_failed(user: UserAccount, *, reason: str) -> AuditEntry:
    """A wrong password or code for a real account. Unknown emails are not recorded here: they
    would let anyone fill the trail, and the access log and rate limiter already cover them."""
    return audit.system(
        "identity.sign_in_failed",
        target_type="user",
        target_id=user.id,
        tenant_id=user.tenant_id,
        actor_realm=user.realm,
        reason=reason,
        locked=user.locked_until is not None,
    )
