"""The gate: visitors signing in and out, and releasing students to the right adults.

A student is released only to one of their guardians or to someone on their authorised
collector list (kept by the guardians or the school office, optionally for a single day). If the
adult at the gate isn't on the list, the guard records a refusal; the guardians are told either
way.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from app.domain.shared.errors import InvalidStateTransition, ValidationFailed
from app.domain.shared.ids import new_id

MAX_COLLECTORS = 10


def _name(value: str, field_name: str) -> str:
    text = " ".join(value.split())
    if not 2 <= len(text) <= 120:
        raise ValidationFailed("Enter a full name.", details={"field": field_name})
    return text


def _short(value: str | None, limit: int) -> str | None:
    text = " ".join((value or "").split())[:limit]
    return text or None


def _phone(value: str | None) -> str | None:
    text = "".join(ch for ch in (value or "") if ch.isdigit() or ch == "+")
    if not text:
        return None
    if not 7 <= len(text.lstrip("+")) <= 15:
        raise ValidationFailed("Enter a valid phone number.", details={"field": "phone"})
    return text


@dataclass(kw_only=True, eq=False)
class Collector:
    id: UUID
    tenant_id: UUID
    student_id: UUID
    name: str
    relationship: str
    phone: str | None
    id_document: str | None  # e.g. "Driver's licence ending 4471"
    valid_on: date | None  # a single day, for a one-off collection; None means until removed
    active: bool
    added_by: UUID
    added_at: datetime

    @classmethod
    def add(
        cls,
        *,
        tenant_id: UUID,
        student_id: UUID,
        name: str,
        relationship: str,
        phone: str | None,
        id_document: str | None,
        valid_on: date | None,
        today: date,
        by: UUID,
        now: datetime,
    ) -> Collector:
        if valid_on is not None and valid_on < today:
            raise ValidationFailed("That day has passed.", details={"field": "valid_on"})
        rel = _short(relationship, 40)
        if rel is None:
            raise ValidationFailed("Say how they're related.", details={"field": "relationship"})
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            student_id=student_id,
            name=_name(name, "name"),
            relationship=rel,
            phone=_phone(phone),
            id_document=_short(id_document, 80),
            valid_on=valid_on,
            active=True,
            added_by=by,
            added_at=now,
        )

    def allowed_on(self, day: date) -> bool:
        return self.active and (self.valid_on is None or self.valid_on == day)


class ReleaseStatus(StrEnum):
    RELEASED = "released"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class Release:
    """One decision at the gate. Append-only."""

    id: UUID
    tenant_id: UUID
    student_id: UUID
    status: ReleaseStatus
    guardian_id: UUID | None
    collector_id: UUID | None
    collector_name: str
    note: str | None
    by: UUID
    at: datetime

    @classmethod
    def decide(
        cls,
        *,
        tenant_id: UUID,
        student_id: UUID,
        guardian_id: UUID | None,
        collector: Collector | None,
        refused_name: str | None,
        note: str | None,
        today: date,
        by: UUID,
        now: datetime,
    ) -> Release:
        chosen = sum(x is not None for x in (guardian_id, collector, refused_name))
        if chosen != 1:
            raise ValidationFailed(
                "Pick the guardian or authorised adult, or record who was refused."
            )
        if collector is not None:
            if collector.student_id != student_id or not collector.allowed_on(today):
                raise ValidationFailed(
                    "That person isn't authorised to collect this student today.",
                    code="not_authorised",
                )
            return cls(
                id=new_id(),
                tenant_id=tenant_id,
                student_id=student_id,
                status=ReleaseStatus.RELEASED,
                guardian_id=None,
                collector_id=collector.id,
                collector_name=collector.name,
                note=_short(note, 300),
                by=by,
                at=now,
            )
        if guardian_id is not None:
            return cls(
                id=new_id(),
                tenant_id=tenant_id,
                student_id=student_id,
                status=ReleaseStatus.RELEASED,
                guardian_id=guardian_id,
                collector_id=None,
                collector_name="",  # filled with the guardian's name by the use case
                note=_short(note, 300),
                by=by,
                at=now,
            )
        clean_note = _short(note, 300)
        if clean_note is None:
            raise ValidationFailed("Say what happened.", details={"field": "note"})
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            student_id=student_id,
            status=ReleaseStatus.REFUSED,
            guardian_id=None,
            collector_id=None,
            collector_name=_name(refused_name or "", "refused_name"),
            note=clean_note,
            by=by,
            at=now,
        )


@dataclass(kw_only=True, eq=False)
class Visit:
    """A visitor's time on site."""

    id: UUID
    tenant_id: UUID
    name: str
    phone: str | None
    organisation: str | None
    purpose: str
    host_id: UUID | None
    badge: str | None
    vehicle: str | None
    signed_in_at: datetime
    signed_in_by: UUID
    signed_out_at: datetime | None = None
    signed_out_by: UUID | None = None

    @classmethod
    def sign_in(
        cls,
        *,
        tenant_id: UUID,
        name: str,
        phone: str | None,
        organisation: str | None,
        purpose: str,
        host_id: UUID | None,
        badge: str | None,
        vehicle: str | None,
        by: UUID,
        now: datetime,
    ) -> Visit:
        why = _short(purpose, 200)
        if why is None:
            raise ValidationFailed("Say why they're visiting.", details={"field": "purpose"})
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            name=_name(name, "name"),
            phone=_phone(phone),
            organisation=_short(organisation, 120),
            purpose=why,
            host_id=host_id,
            badge=_short(badge, 20),
            vehicle=_short(vehicle, 20),
            signed_in_at=now,
            signed_in_by=by,
        )

    def sign_out(self, *, by: UUID, now: datetime) -> None:
        if self.signed_out_at is not None:
            raise InvalidStateTransition("Already signed out.", code="already_signed_out")
        self.signed_out_at = now
        self.signed_out_by = by
