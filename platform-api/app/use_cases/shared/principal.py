"""The authenticated caller, as seen by use cases."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.identity.permissions import PermissionSet
from app.domain.identity.roles import Realm
from app.domain.shared.errors import PermissionDenied
from app.use_cases.shared.ports import AccessClaims


@dataclass(frozen=True, slots=True)
class Principal:
    realm: Realm
    user_id: UUID
    tenant_id: UUID | None
    roles: frozenset[str]
    permissions: PermissionSet
    session_family: UUID
    tenant_config_version: int | None = None

    @classmethod
    def from_claims(cls, claims: AccessClaims) -> Principal:
        return cls(
            realm=claims.realm,
            user_id=claims.user_id,
            tenant_id=claims.tenant_id,
            roles=claims.roles,
            permissions=PermissionSet.for_realm(
                claims.realm, claims.roles, claims.capabilities, primary_owner=claims.primary_owner
            ),
            session_family=claims.session_family,
            tenant_config_version=claims.tenant_config_version,
        )

    def require(self, *permissions: str) -> None:
        missing = [p for p in permissions if not self.permissions.allows(p)]
        if missing:
            raise PermissionDenied(
                "You do not have permission to do this.", details={"missing": missing}
            )

    def require_realm(self, realm: Realm) -> None:
        if self.realm is not realm:
            raise PermissionDenied("This action is not available for this account type.")

    @property
    def school_tenant_id(self) -> UUID:
        """Tenant of a school principal. Raises if called for a platform principal."""
        if self.realm is not Realm.SCHOOL or self.tenant_id is None:
            raise PermissionDenied("This action needs a school account.")
        return self.tenant_id
