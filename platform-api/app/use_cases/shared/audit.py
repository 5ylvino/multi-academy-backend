"""Helpers for writing audit entries consistently.

Every change a person makes is recorded in the same transaction as the change itself (see
``UnitOfWork.audit``), so the trail can never show something that was rolled back, or miss
something that was saved. Request details (request id, IP, device) are added by the unit of work.

Never put secrets in metadata: no passwords, tokens, codes, seeds or file contents.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from app.domain.identity.roles import Realm
from app.use_cases.shared.ports import AuditEntry
from app.use_cases.shared.principal import Principal


def by(
    actor: Principal,
    action: str,
    *,
    target_type: str,
    target_id: UUID | str,
    tenant_id: UUID | None = None,
    **metadata: Any,
) -> AuditEntry:
    """An entry for something ``actor`` did. School actors default to their own school."""
    return AuditEntry(
        action=action,
        actor_id=actor.user_id,
        actor_realm=actor.realm,
        tenant_id=tenant_id if tenant_id is not None else actor.tenant_id,
        target_type=target_type,
        target_id=str(target_id),
        metadata=metadata,
    )


def system(
    action: str,
    *,
    target_type: str,
    target_id: UUID | str,
    tenant_id: UUID | None,
    actor_id: UUID | None = None,
    actor_realm: Realm | None = None,
    **metadata: Any,
) -> AuditEntry:
    """An entry before a principal exists (sign-in, signup) or for automatic work."""
    return AuditEntry(
        action=action,
        actor_id=actor_id,
        actor_realm=actor_realm,
        tenant_id=tenant_id,
        target_type=target_type,
        target_id=str(target_id),
        metadata=metadata,
    )


def changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Field-level difference for an update: ``{"name": {"from": "JSS 1A", "to": "JSS 1B"}}``."""
    return {
        key: {"from": before.get(key), "to": after.get(key)}
        for key in sorted(set(before) | set(after))
        if before.get(key) != after.get(key)
    }
