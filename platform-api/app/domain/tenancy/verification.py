"""Email verification for a newly registered school."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.domain.shared.entity import Entity
from app.domain.shared.errors import ValidationFailed
from app.domain.shared.ids import new_id


@dataclass(kw_only=True, eq=False)
class EmailVerification(Entity):
    tenant_id: UUID
    user_id: UUID
    code_hash: str
    expires_at: datetime
    attempts: int = 0
    max_attempts: int = 5
    consumed_at: datetime | None = None

    @classmethod
    def issue(
        cls, *, tenant_id: UUID, user_id: UUID, code_hash: str, now: datetime, ttl: timedelta
    ) -> EmailVerification:
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            user_id=user_id,
            code_hash=code_hash,
            expires_at=now + ttl,
        )

    def ensure_usable(self, now: datetime) -> None:
        if self.consumed_at is not None:
            raise ValidationFailed("This code was already used.", code="verification_used")
        if self.expires_at <= now:
            raise ValidationFailed(
                "This code has expired. Request a new one.", code="verification_expired"
            )
        if self.attempts >= self.max_attempts:
            raise ValidationFailed(
                "Too many wrong codes. Request a new one.", code="verification_exhausted"
            )

    def record_wrong_code(self) -> None:
        self.attempts += 1

    def consume(self, now: datetime) -> None:
        self.consumed_at = now
