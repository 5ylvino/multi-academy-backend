"""Operations persistence, scoped to the current school.

Clinic notes are stored encrypted: the repository holds ciphertext, and the use case seals and
opens it through the encryption port.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.operations.clinic import Outcome
from app.domain.operations.gate import Collector, Release, Visit
from app.domain.shared.value_objects import Page


@dataclass(frozen=True, slots=True)
class StoredHealthRecord:
    student_id: UUID
    blood_group: str | None
    genotype: str | None
    details_ciphertext: str
    alert_ciphertext: str | None
    updated_by: UUID
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredVisit:
    id: UUID
    student_id: UUID
    arrived_at: datetime
    temperature_dc: int | None
    outcome: Outcome | None
    left_at: datetime | None
    recorded_by: UUID
    notes_ciphertext: str


class ClinicRepository(Protocol):
    async def get_record(self, student_id: UUID) -> StoredHealthRecord | None: ...

    async def save_record(self, record: StoredHealthRecord) -> None:
        """Insert or replace."""
        ...

    async def add_visit(self, visit: StoredVisit) -> None: ...

    async def get_visit(
        self, visit_id: UUID, *, for_update: bool = False
    ) -> StoredVisit | None: ...

    async def save_visit(self, visit: StoredVisit) -> None: ...

    async def visits(
        self,
        *,
        student_id: UUID | None,
        open_only: bool,
        since: datetime | None,
        cursor: str | None,
        limit: int,
    ) -> Page[StoredVisit]:
        """Newest first."""
        ...


class GateRepository(Protocol):
    async def add_collector(self, collector: Collector) -> None: ...

    async def get_collector(self, collector_id: UUID) -> Collector | None: ...

    async def save_collector(self, collector: Collector) -> None: ...

    async def collectors_of(
        self, student_id: UUID, *, include_inactive: bool
    ) -> list[Collector]: ...

    async def add_release(self, release: Release) -> None: ...

    async def releases(
        self, *, student_id: UUID | None, since: datetime | None, cursor: str | None, limit: int
    ) -> Page[Release]:
        """Newest first."""
        ...

    async def add_visit(self, visit: Visit) -> None: ...

    async def get_visit(self, visit_id: UUID, *, for_update: bool = False) -> Visit | None: ...

    async def save_visit(self, visit: Visit) -> None: ...

    async def visits(
        self, *, on_site_only: bool, since: datetime | None, cursor: str | None, limit: int
    ) -> Page[Visit]: ...
