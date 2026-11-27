"""Operations persistence, scoped to the current school.

Clinic notes are stored encrypted: the repository holds ciphertext, and the use case seals and
opens it through the encryption port.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from app.domain.operations.clinic import Outcome
from app.domain.operations.gate import Collector, Release, Visit
from app.domain.operations.hostel import Allocation, Exeat, ExeatStatus, Hostel, Room
from app.domain.operations.transport import (
    Assignment,
    Boarding,
    BoardingKind,
    Direction,
    Route,
    Stop,
)
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


class TransportRepository(Protocol):
    async def add_route(self, route: Route) -> None: ...

    async def get_route(self, route_id: UUID, *, for_update: bool = False) -> Route | None: ...

    async def route_by_code(self, code: str) -> Route | None: ...

    async def save_route(self, route: Route) -> None: ...

    async def routes(self, *, include_inactive: bool) -> list[Route]: ...

    async def routes_driven_by(self, driver_id: UUID) -> list[Route]: ...

    async def rider_counts(self) -> dict[UUID, int]:
        """Students assigned to each route."""
        ...

    async def add_stop(self, stop: Stop) -> None: ...

    async def get_stop(self, stop_id: UUID) -> Stop | None: ...

    async def save_stop(self, stop: Stop) -> None: ...

    async def stops(self, route_id: UUID) -> list[Stop]:
        """Active stops, in order along the route."""
        ...

    async def stop_in_use(self, stop_id: UUID) -> bool: ...

    async def assignment(self, student_id: UUID) -> Assignment | None: ...

    async def save_assignment(self, assignment: Assignment) -> None:
        """Insert or replace the student's assignment."""
        ...

    async def remove_assignment(self, student_id: UUID) -> None: ...

    async def assignments(self, route_id: UUID) -> list[Assignment]: ...

    async def add_boarding(self, boarding: Boarding) -> None: ...

    async def last_events(
        self, route_id: UUID, day: date, direction: Direction
    ) -> dict[UUID, Boarding]:
        """Each student's latest event on this trip, by student."""
        ...

    async def last_event(
        self, student_id: UUID, day: date, direction: Direction
    ) -> BoardingKind | None: ...

    async def boardings(
        self, *, student_id: UUID, since: datetime, cursor: str | None, limit: int
    ) -> Page[Boarding]: ...


class HostelRepository(Protocol):
    async def add_hostel(self, hostel: Hostel) -> None: ...

    async def get_hostel(self, hostel_id: UUID) -> Hostel | None: ...

    async def save_hostel(self, hostel: Hostel) -> None: ...

    async def hostels(self, *, include_inactive: bool) -> list[Hostel]: ...

    async def add_room(self, room: Room) -> None: ...

    async def get_room(self, room_id: UUID, *, for_update: bool = False) -> Room | None: ...

    async def save_room(self, room: Room) -> None: ...

    async def rooms(self, hostel_id: UUID) -> list[Room]:
        """Active rooms, by name."""
        ...

    async def occupancy(self) -> dict[UUID, int]:
        """Boarders in each room."""
        ...

    async def allocation(self, student_id: UUID) -> Allocation | None: ...

    async def bed_holder(self, room_id: UUID, bed: str) -> UUID | None: ...

    async def save_allocation(self, allocation: Allocation) -> None:
        """Insert or replace the boarder's allocation."""
        ...

    async def remove_allocation(self, student_id: UUID) -> None: ...

    async def allocations(self, hostel_id: UUID) -> list[Allocation]: ...

    async def add_exeat(self, exeat: Exeat) -> None: ...

    async def get_exeat(self, exeat_id: UUID, *, for_update: bool = False) -> Exeat | None: ...

    async def save_exeat(self, exeat: Exeat) -> None: ...

    async def students_out(self) -> set[UUID]:
        """Boarders away on an exeat right now."""
        ...

    async def open_exeat(self, student_id: UUID) -> Exeat | None:
        """The boarder's pending, approved or current exeat, if any."""
        ...

    async def exeats(
        self,
        *,
        student_id: UUID | None,
        statuses: frozenset[ExeatStatus] | None,
        overdue_at: datetime | None,
        cursor: str | None,
        limit: int,
    ) -> Page[Exeat]:
        """Newest first. ``overdue_at``: only boarders still out after their return time."""
        ...
