"""Second factor for sign-in: an authenticator-app secret plus one-time recovery codes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.domain.shared.entity import Entity
from app.domain.shared.errors import Conflict, InvalidStateTransition
from app.domain.shared.ids import new_id

RECOVERY_CODE_COUNT = 10
RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i


def normalise_recovery_code(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum())


def looks_like_recovery_code(code: str) -> bool:
    cleaned = normalise_recovery_code(code)
    return len(cleaned) == 10 and all(ch in RECOVERY_ALPHABET for ch in cleaned)


@dataclass(kw_only=True, eq=False)
class TotpFactor(Entity):
    """One per user. ``secret_ciphertext`` is encrypted at rest; this layer never sees the key."""

    user_id: UUID
    secret_ciphertext: str
    created_at: datetime
    confirmed_at: datetime | None = None
    last_used_step: int | None = None
    recovery_code_hashes: list[str] = field(default_factory=list)

    @classmethod
    def pending(cls, *, user_id: UUID, secret_ciphertext: str, now: datetime) -> TotpFactor:
        return cls(
            id=new_id(), user_id=user_id, secret_ciphertext=secret_ciphertext, created_at=now
        )

    @property
    def confirmed(self) -> bool:
        return self.confirmed_at is not None

    def confirm(self, *, step: int, now: datetime, recovery_code_hashes: list[str]) -> None:
        if self.confirmed:
            raise Conflict("Two-step verification is already set up.", code="mfa_already_enabled")
        self.confirmed_at = now
        self.use_step(step)
        self.recovery_code_hashes = list(recovery_code_hashes)

    def use_step(self, step: int) -> None:
        if self.last_used_step is not None and step <= self.last_used_step:
            raise InvalidStateTransition("This code was already used.", code="mfa_code_reused")
        self.last_used_step = step

    def consume_recovery_code(self, code_hash: str) -> bool:
        if code_hash in self.recovery_code_hashes:
            self.recovery_code_hashes.remove(code_hash)
            return True
        return False

    def replace_recovery_codes(self, hashes: list[str]) -> None:
        if not self.confirmed:
            raise InvalidStateTransition("Finish setting up two-step verification first.")
        self.recovery_code_hashes = list(hashes)
