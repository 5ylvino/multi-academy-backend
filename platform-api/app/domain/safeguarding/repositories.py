"""Safeguarding persistence, scoped to the current school.

Repositories store ``summary`` and entry ``text`` encrypted; the use case passes plaintext in
and gets plaintext back, through the encryption port it was given.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.safeguarding.concerns import Concern, Entry
from app.domain.shared.value_objects import Page


class SafeguardingRepository(Protocol):
    async def add(self, concern: Concern, *, summary_ciphertext: str) -> None: ...

    async def get(
        self, concern_id: UUID, *, for_update: bool = False
    ) -> tuple[Concern, str] | None:
        """The concern (``summary`` empty) and its encrypted summary."""
        ...

    async def save(self, concern: Concern, *, entry_ciphertexts: dict[UUID, str]) -> None:
        """Store the status and lead, and append ``new_entries`` with their encrypted text."""
        ...

    async def entries(self, concern_id: UUID) -> list[tuple[Entry, str]]:
        """The chronology, oldest first, with each entry's encrypted text (``text`` empty)."""
        ...

    async def search(
        self,
        *,
        status: str | None,
        student_id: UUID | None,
        reported_by: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> Page[Concern]:
        """Newest first. ``summary`` is not loaded."""
        ...

    async def counts(self) -> dict[str, int]:
        """Cases by status, and ``urgent_open``."""
        ...
