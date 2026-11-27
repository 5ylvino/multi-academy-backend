"""Argon2id password hashing, run off the event loop because it is CPU-bound by design."""

from __future__ import annotations

import asyncio

from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_DUMMY_PASSWORD = "timing-equaliser-password"  # noqa: S105 - not a credential


class Argon2PasswordHasher:
    """OWASP-recommended Argon2id parameters (m=19 MiB, t=2, p=1) unless tuned otherwise."""

    def __init__(
        self, *, time_cost: int = 2, memory_cost: int = 19_456, parallelism: int = 1
    ) -> None:
        self._hasher = _Argon2(
            time_cost=time_cost, memory_cost=memory_cost, parallelism=parallelism
        )
        self._dummy_hash = self._hasher.hash(_DUMMY_PASSWORD)

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._hasher.hash, password)

    async def verify(self, password_hash: str, password: str) -> bool:
        return await asyncio.to_thread(self._verify_sync, password_hash, password)

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True

    async def burn_time(self) -> None:
        await self.verify(self._dummy_hash, "not-the-password")

    def _verify_sync(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
