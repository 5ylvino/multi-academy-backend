"""School transport: bus routes with ordered stops, the students on each route, and boarding.

The driver works from the day's manifest. Each time a child gets on or off the bus, or isn't at
the stop, the driver records it and the child's guardians are told in the app. Boarding records
are append-only, so the school can always show who was on the bus and when.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from uuid import UUID

from app.domain.shared.errors import InvalidStateTransition, ValidationFailed
from app.domain.shared.ids import new_id

MAX_STOPS = 40
_PLATE = re.compile(r"^[A-Z0-9][A-Z0-9 -]{1,14}$")


def _text(value: str | None, limit: int, field_name: str, *, required: bool = False) -> str | None:
    text = " ".join((value or "").split())
    if required and not text:
        raise ValidationFailed("This is required.", details={"field": field_name})
    if len(text) > limit:
        raise ValidationFailed("That is too long.", details={"field": field_name})
    return text or None


def _capacity(value: int) -> int:
    if not 1 <= value <= 120:
        raise ValidationFailed("Seats must be between 1 and 120.", details={"field": "capacity"})
    return value


def _plate(value: str | None) -> str | None:
    text = " ".join((value or "").upper().split())
    if not text:
        return None
    if not _PLATE.match(text):
        raise ValidationFailed("Enter the vehicle's plate number.", details={"field": "plate"})
    return text


@dataclass(kw_only=True, eq=False)
class Route:
    id: UUID
    tenant_id: UUID
    name: str
    code: str
    vehicle_plate: str | None
    capacity: int
    driver_id: UUID | None
    active: bool
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        name: str,
        code: str,
        vehicle_plate: str | None,
        capacity: int,
        driver_id: UUID | None,
        now: datetime,
    ) -> Route:
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            name=_text(name, 80, "name", required=True) or "",
            code=_code(code),
            vehicle_plate=_plate(vehicle_plate),
            capacity=_capacity(capacity),
            driver_id=driver_id,
            active=True,
            created_at=now,
        )

    def change(
        self,
        *,
        name: str,
        vehicle_plate: str | None,
        capacity: int,
        driver_id: UUID | None,
        active: bool,
        riders: int,
    ) -> None:
        seats = _capacity(capacity)
        if seats < riders:
            raise ValidationFailed(
                f"{riders} students ride this route; move some before reducing the seats.",
                details={"field": "capacity"},
                code="over_capacity",
            )
        if not active and riders:
            raise InvalidStateTransition(
                "Move the students on this route first.", code="route_in_use"
            )
        self.name = _text(name, 80, "name", required=True) or ""
        self.vehicle_plate = _plate(vehicle_plate)
        self.capacity = seats
        self.driver_id = driver_id
        self.active = active


def _code(value: str) -> str:
    text = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9-]{1,12}", text):
        raise ValidationFailed("Use up to 12 letters, digits or dashes.", details={"field": "code"})
    return text


@dataclass(kw_only=True, eq=False)
class Stop:
    id: UUID
    tenant_id: UUID
    route_id: UUID
    name: str
    position: int
    morning_at: time | None
    afternoon_at: time | None
    landmark: str | None
    active: bool = True

    @classmethod
    def add(
        cls,
        *,
        tenant_id: UUID,
        route_id: UUID,
        name: str,
        position: int,
        morning_at: time | None,
        afternoon_at: time | None,
        landmark: str | None,
    ) -> Stop:
        stop = cls(
            id=new_id(),
            tenant_id=tenant_id,
            route_id=route_id,
            name="",
            position=1,
            morning_at=None,
            afternoon_at=None,
            landmark=None,
        )
        stop.change(
            name=name,
            position=position,
            morning_at=morning_at,
            afternoon_at=afternoon_at,
            landmark=landmark,
        )
        return stop

    def change(
        self,
        *,
        name: str,
        position: int,
        morning_at: time | None,
        afternoon_at: time | None,
        landmark: str | None,
    ) -> None:
        if not 1 <= position <= MAX_STOPS:
            raise ValidationFailed(
                f"Position must be between 1 and {MAX_STOPS}.", details={"field": "position"}
            )
        self.name = _text(name, 80, "name", required=True) or ""
        self.position = position
        self.morning_at = morning_at
        self.afternoon_at = afternoon_at
        self.landmark = _text(landmark, 120, "landmark")


@dataclass(frozen=True, slots=True)
class Assignment:
    """The route and stop a student uses, both ways. One per student."""

    tenant_id: UUID
    student_id: UUID
    route_id: UUID
    stop_id: UUID
    by: UUID
    at: datetime


class Direction(StrEnum):
    TO_SCHOOL = "to_school"
    FROM_SCHOOL = "from_school"


class BoardingKind(StrEnum):
    BOARDED = "boarded"
    DROPPED = "dropped"
    ABSENT = "absent"  # not at the stop (morning) or not on the bus at home time


#: What may come before each event on one trip. A child marked absent can still board late.
_ALLOWED_AFTER: dict[BoardingKind, frozenset[BoardingKind | None]] = {
    BoardingKind.BOARDED: frozenset({None, BoardingKind.ABSENT}),
    BoardingKind.DROPPED: frozenset({BoardingKind.BOARDED}),
    BoardingKind.ABSENT: frozenset({None}),
}


@dataclass(frozen=True, slots=True)
class Boarding:
    """One event on the bus. Append-only."""

    id: UUID
    tenant_id: UUID
    student_id: UUID
    route_id: UUID
    stop_id: UUID | None
    direction: Direction
    kind: BoardingKind
    day: date
    by: UUID
    at: datetime

    @classmethod
    def record(
        cls,
        *,
        tenant_id: UUID,
        student_id: UUID,
        route_id: UUID,
        stop_id: UUID | None,
        direction: Direction,
        kind: BoardingKind,
        previous: BoardingKind | None,
        day: date,
        by: UUID,
        now: datetime,
    ) -> Boarding:
        """``previous`` is the student's last event on this trip (same day and direction)."""
        if previous not in _ALLOWED_AFTER[kind]:
            message = {
                BoardingKind.BOARDED: "Already on the bus for this trip.",
                BoardingKind.DROPPED: "Record that they boarded first.",
                BoardingKind.ABSENT: "Already recorded for this trip.",
            }[kind]
            raise InvalidStateTransition(message, code="boarding_out_of_order")
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            student_id=student_id,
            route_id=route_id,
            stop_id=stop_id,
            direction=direction,
            kind=kind,
            day=day,
            by=by,
            at=now,
        )
