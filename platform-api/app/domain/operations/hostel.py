"""Boarding houses: hostels and rooms, which boarder sleeps where, and exeats (leave passes).

An exeat lets a boarder leave the school for a stated time with a named adult. A guardian (or
the boarding staff) asks for it; someone other than the person who asked approves it; the
hostel signs the child out and back in. A boarder still out after their return time is overdue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from app.domain.shared.errors import InvalidStateTransition, ValidationFailed
from app.domain.shared.ids import new_id

MAX_EXEAT = timedelta(days=30)


def _text(value: str | None, limit: int, field_name: str, *, required: bool = False) -> str | None:
    text = " ".join((value or "").split())
    if required and not text:
        raise ValidationFailed("This is required.", details={"field": field_name})
    if len(text) > limit:
        raise ValidationFailed("That is too long.", details={"field": field_name})
    return text or None


class HostelKind(StrEnum):
    BOYS = "boys"
    GIRLS = "girls"
    MIXED = "mixed"


@dataclass(kw_only=True, eq=False)
class Hostel:
    id: UUID
    tenant_id: UUID
    name: str
    kind: HostelKind
    warden_id: UUID | None
    active: bool
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        name: str,
        kind: HostelKind,
        warden_id: UUID | None,
        now: datetime,
    ) -> Hostel:
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            name=_text(name, 80, "name", required=True) or "",
            kind=kind,
            warden_id=warden_id,
            active=True,
            created_at=now,
        )

    def change(self, *, name: str, warden_id: UUID | None, active: bool, residents: int) -> None:
        if not active and residents:
            raise InvalidStateTransition(
                "Move the boarders out of this hostel first.", code="hostel_in_use"
            )
        self.name = _text(name, 80, "name", required=True) or ""
        self.warden_id = warden_id
        self.active = active


def _capacity(value: int) -> int:
    if not 1 <= value <= 40:
        raise ValidationFailed("Beds must be between 1 and 40.", details={"field": "capacity"})
    return value


@dataclass(kw_only=True, eq=False)
class Room:
    id: UUID
    tenant_id: UUID
    hostel_id: UUID
    name: str
    capacity: int
    active: bool

    @classmethod
    def add(cls, *, tenant_id: UUID, hostel_id: UUID, name: str, capacity: int) -> Room:
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            hostel_id=hostel_id,
            name=_text(name, 40, "name", required=True) or "",
            capacity=_capacity(capacity),
            active=True,
        )

    def change(self, *, name: str, capacity: int, active: bool, occupants: int) -> None:
        beds = _capacity(capacity)
        if beds < occupants:
            raise ValidationFailed(
                f"{occupants} boarders sleep here; move some before removing beds.",
                details={"field": "capacity"},
                code="over_capacity",
            )
        if not active and occupants:
            raise InvalidStateTransition("Move the boarders out first.", code="room_in_use")
        self.name = _text(name, 40, "name", required=True) or ""
        self.capacity = beds
        self.active = active


@dataclass(frozen=True, slots=True)
class Allocation:
    """Where a boarder sleeps. One per student."""

    tenant_id: UUID
    student_id: UUID
    room_id: UUID
    bed: str | None
    by: UUID
    at: datetime


def bed_label(value: str | None) -> str | None:
    return _text(value, 10, "bed")


class ExeatStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    OUT = "out"
    RETURNED = "returned"


OPEN_EXEAT = frozenset({ExeatStatus.PENDING, ExeatStatus.APPROVED, ExeatStatus.OUT})


@dataclass(kw_only=True, eq=False)
class Exeat:
    id: UUID
    tenant_id: UUID
    student_id: UUID
    requested_by: UUID
    reason: str
    collector_name: str
    leave_at: datetime
    return_by: datetime
    status: ExeatStatus
    created_at: datetime
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    left_at: datetime | None = None
    returned_at: datetime | None = None

    @classmethod
    def request(
        cls,
        *,
        tenant_id: UUID,
        student_id: UUID,
        reason: str,
        collector_name: str,
        leave_at: datetime,
        return_by: datetime,
        by: UUID,
        now: datetime,
    ) -> Exeat:
        if leave_at < now - timedelta(hours=1):
            raise ValidationFailed("That time has passed.", details={"field": "leave_at"})
        if return_by <= leave_at:
            raise ValidationFailed(
                "The return time must be after leaving.", details={"field": "return_by"}
            )
        if return_by - leave_at > MAX_EXEAT:
            raise ValidationFailed(
                "An exeat can be at most 30 days.", details={"field": "return_by"}
            )
        collector = " ".join(collector_name.split())
        if not 2 <= len(collector) <= 120:
            raise ValidationFailed(
                "Who is collecting them? Enter a full name.",
                details={"field": "collector_name"},
            )
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            student_id=student_id,
            requested_by=by,
            reason=_text(reason, 500, "reason", required=True) or "",
            collector_name=collector,
            leave_at=leave_at,
            return_by=return_by,
            status=ExeatStatus.PENDING,
            created_at=now,
        )

    def _decide(self, status: ExeatStatus, *, by: UUID, note: str | None, now: datetime) -> None:
        if self.status is not ExeatStatus.PENDING:
            raise InvalidStateTransition("This exeat has been decided.", code="exeat_decided")
        if by == self.requested_by:
            raise InvalidStateTransition(
                "Someone other than the person who asked must decide.", code="self_approval"
            )
        self.status = status
        self.decided_by = by
        self.decided_at = now
        self.decision_note = _text(note, 500, "note")

    def approve(self, *, by: UUID, note: str | None, now: datetime) -> None:
        self._decide(ExeatStatus.APPROVED, by=by, note=note, now=now)

    def reject(self, *, by: UUID, note: str, now: datetime) -> None:
        if not _text(note, 500, "note"):
            raise ValidationFailed("Say why, for the family.", details={"field": "note"})
        self._decide(ExeatStatus.REJECTED, by=by, note=note, now=now)

    def cancel(self, *, now: datetime) -> None:
        if self.status not in {ExeatStatus.PENDING, ExeatStatus.APPROVED}:
            raise InvalidStateTransition(
                "Only an exeat that hasn't started can be cancelled.", code="exeat_started"
            )
        self.status = ExeatStatus.CANCELLED
        self.decided_at = self.decided_at or now

    def sign_out(self, *, now: datetime) -> None:
        if self.status is not ExeatStatus.APPROVED:
            raise InvalidStateTransition("This exeat isn't approved.", code="exeat_not_approved")
        if now > self.return_by:
            raise InvalidStateTransition(
                "This exeat has expired. Ask for a new one.", code="exeat_expired"
            )
        self.status = ExeatStatus.OUT
        self.left_at = now

    def sign_in(self, *, now: datetime) -> None:
        if self.status is not ExeatStatus.OUT:
            raise InvalidStateTransition("This boarder isn't out.", code="exeat_not_out")
        self.status = ExeatStatus.RETURNED
        self.returned_at = now

    def overdue(self, now: datetime) -> bool:
        return self.status is ExeatStatus.OUT and now > self.return_by

    @property
    def returned_late(self) -> bool:
        return self.returned_at is not None and self.returned_at > self.return_by
