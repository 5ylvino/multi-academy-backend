"""Permission checks shared by both realms."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.domain.identity.platform_catalog import PLATFORM_ROLE_PERMISSIONS
from app.domain.identity.roles import PlatformRole, Realm, SchoolRole
from app.domain.identity.school_catalog import (
    KNOWN_SCHOOL_PERMISSIONS,
    ROLE_ONLY_CAPABILITIES,
    SCHOOL_ROLE_PERMISSIONS,
)
from app.domain.shared.errors import ValidationFailed

WILDCARD = "*"


@dataclass(frozen=True, slots=True)
class PermissionSet:
    """The effective grants of one principal.

    ``resource:manage`` and ``resource:*`` imply every action on that resource, and ``*`` implies
    everything. This mirrors the semantics clients already rely on.
    """

    grants: frozenset[str]

    def allows(self, permission: str) -> bool:
        if WILDCARD in self.grants or permission in self.grants:
            return True
        resource, _, _ = permission.partition(":")
        return f"{resource}:manage" in self.grants or f"{resource}:*" in self.grants

    def allows_all(self, permissions: Iterable[str]) -> bool:
        return all(self.allows(p) for p in permissions)

    @classmethod
    def for_school(
        cls,
        roles: Iterable[SchoolRole],
        capabilities: Iterable[str] = (),
        *,
        primary_owner: bool = False,
    ) -> PermissionSet:
        grants: set[str] = set()
        for role in roles:
            grants |= SCHOOL_ROLE_PERMISSIONS[role]
        grants |= set(capabilities)
        if primary_owner:
            grants.add(WILDCARD)
        return cls(frozenset(grants))

    @classmethod
    def for_platform(cls, roles: Iterable[PlatformRole]) -> PermissionSet:
        grants: set[str] = set()
        for role in roles:
            grants |= PLATFORM_ROLE_PERMISSIONS[role]
        return cls(frozenset(grants))

    @classmethod
    def for_realm(
        cls,
        realm: Realm,
        roles: Iterable[str],
        capabilities: Iterable[str] = (),
        *,
        primary_owner: bool = False,
    ) -> PermissionSet:
        if realm is Realm.PLATFORM:
            return cls.for_platform(PlatformRole(r) for r in roles)
        return cls.for_school(
            (SchoolRole(r) for r in roles), capabilities, primary_owner=primary_owner
        )


def parse_school_roles(values: Iterable[str]) -> frozenset[SchoolRole]:
    roles: set[SchoolRole] = set()
    unknown: list[str] = []
    for value in values:
        try:
            roles.add(SchoolRole(value))
        except ValueError:
            unknown.append(value)
    if unknown:
        raise ValidationFailed("Unknown role.", details={"roles": sorted(unknown)})
    if not roles:
        raise ValidationFailed("At least one role is required.", details={"field": "roles"})
    return frozenset(roles)


def validate_custom_capabilities(values: Iterable[str]) -> frozenset[str]:
    """Custom capabilities must be known, must not be wildcards and must not be role-only powers."""
    caps = frozenset(v.strip() for v in values)
    unknown = sorted(c for c in caps if not c or c == WILDCARD or c not in KNOWN_SCHOOL_PERMISSIONS)
    if unknown:
        raise ValidationFailed("Unknown capability.", details={"capabilities": unknown})
    role_only = sorted(caps & ROLE_ONLY_CAPABILITIES)
    if role_only:
        raise ValidationFailed(
            "These powers come only with their role and cannot be granted directly.",
            details={"capabilities": role_only},
        )
    return caps
