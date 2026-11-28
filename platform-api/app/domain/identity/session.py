"""Refresh sessions with rotation and reuse detection.

Each login starts a *family*. Every refresh rotates the token: the old one is marked replaced and a
new one is issued in the same family. If a replaced token is ever presented again, it was stolen
or leaked, so the whole family is revoked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.domain.identity.roles import Realm
from app.domain.shared.entity import Entity
from app.domain.shared.ids import new_id


@dataclass(kw_only=True, eq=False)
class RefreshSession(Entity):
    family_id: UUID
    user_id: UUID
    realm: Realm
    tenant_id: UUID | None
    token_hash: str
    issued_at: datetime
    expires_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    replaced_by: UUID | None = None
    user_agent: str | None = None
    ip_address: str | None = None

    @classmethod
    def start(
        cls,
        *,
        user_id: UUID,
        realm: Realm,
        tenant_id: UUID | None,
        token_hash: str,
        now: datetime,
        ttl: timedelta,
        family_id: UUID | None = None,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> RefreshSession:
        return cls(
            id=new_id(),
            family_id=family_id or new_id(),
            user_id=user_id,
            realm=realm,
            tenant_id=tenant_id,
            token_hash=token_hash,
            issued_at=now,
            expires_at=now + ttl,
            user_agent=user_agent,
            ip_address=ip_address,
        )

    def is_active(self, now: datetime) -> bool:
        return self.revoked_at is None and self.replaced_by is None and self.expires_at > now

    @property
    def was_rotated(self) -> bool:
        return self.replaced_by is not None

    def rotate_to(self, successor: RefreshSession, now: datetime) -> None:
        self.replaced_by = successor.id
        self.last_used_at = now

    def revoke(self, now: datetime) -> None:
        if self.revoked_at is None:
            self.revoked_at = now
