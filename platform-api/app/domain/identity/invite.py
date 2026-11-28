"""Invitations: how staff, parents and students get their first password."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.domain.shared.entity import Entity
from app.domain.shared.errors import Conflict, ValidationFailed
from app.domain.shared.ids import new_id

INVITE_TTL = timedelta(days=7)


@dataclass(kw_only=True, eq=False)
class UserInvite(Entity):
    tenant_id: UUID
    user_id: UUID
    token_hash: str
    expires_at: datetime
    accepted_at: datetime | None = None

    @classmethod
    def issue(cls, *, tenant_id: UUID, user_id: UUID, token_hash: str, now: datetime) -> UserInvite:
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=now + INVITE_TTL,
        )

    def accept(self, now: datetime) -> None:
        if self.accepted_at is not None:
            raise Conflict("This invitation was already used.", code="invite_used")
        if self.expires_at <= now:
            raise ValidationFailed(
                "This invitation has expired. Ask your school for a new one.", code="invite_expired"
            )
        self.accepted_at = now
