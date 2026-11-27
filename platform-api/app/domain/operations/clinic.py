"""The school clinic: each student's health record and every visit to the nurse.

Health information is sensitive. The record and visit notes are encrypted at rest. Teachers see
only the medical alert the school chose to share with staff (allergies and a short note, such as
"asthma: inhaler in bag"); the nurse sees everything; parents see and update their own child's
record and see their child's visits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from app.domain.shared.entity import AggregateRoot
from app.domain.shared.errors import InvalidStateTransition, ValidationFailed
from app.domain.shared.events import DomainEvent
from app.domain.shared.ids import new_id

BLOOD_GROUPS = frozenset({"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"})
GENOTYPES = frozenset({"AA", "AS", "AC", "SS", "SC", "CC"})
MAX_ITEMS = 30


class Outcome(StrEnum):
    RETURNED_TO_CLASS = "returned_to_class"
    RESTED_IN_CLINIC = "rested_in_clinic"
    SENT_HOME = "sent_home"
    COLLECTED_BY_PARENT = "collected_by_parent"
    REFERRED_TO_HOSPITAL = "referred_to_hospital"


#: Outcomes the parents must hear about straight away, by email as well as in the app. A parent
#: who collected the child already knows.
URGENT_OUTCOMES = frozenset({Outcome.SENT_HOME, Outcome.REFERRED_TO_HOSPITAL})


def _items(values: list[str], field_name: str) -> tuple[str, ...]:
    clean: list[str] = []
    for v in values:
        text = " ".join(v.split())[:120]
        if text and text.lower() not in {c.lower() for c in clean}:
            clean.append(text)
    if len(clean) > MAX_ITEMS:
        raise ValidationFailed("That list is too long.", details={"field": field_name})
    return tuple(clean)


def _note(value: str | None, limit: int, field_name: str) -> str | None:
    text = (value or "").strip()
    if len(text) > limit:
        raise ValidationFailed("That is too long.", details={"field": field_name})
    return text or None


@dataclass(kw_only=True, eq=False)
class HealthRecord:
    """One per student. ``id`` is the student's user id."""

    id: UUID
    tenant_id: UUID
    blood_group: str | None
    genotype: str | None
    allergies: tuple[str, ...]
    conditions: tuple[str, ...]
    medications: tuple[str, ...]
    notes: str | None
    #: What teachers and other staff see, e.g. "Severe peanut allergy: EpiPen in office".
    staff_alert: str | None
    doctor: str | None
    updated_by: UUID
    updated_at: datetime

    @classmethod
    def build(
        cls,
        *,
        tenant_id: UUID,
        student_id: UUID,
        blood_group: str | None,
        genotype: str | None,
        allergies: list[str],
        conditions: list[str],
        medications: list[str],
        notes: str | None,
        staff_alert: str | None,
        doctor: str | None,
        by: UUID,
        now: datetime,
    ) -> HealthRecord:
        group = (blood_group or "").strip().upper() or None
        if group is not None and group not in BLOOD_GROUPS:
            raise ValidationFailed("Unknown blood group.", details={"field": "blood_group"})
        geno = (genotype or "").strip().upper() or None
        if geno is not None and geno not in GENOTYPES:
            raise ValidationFailed("Unknown genotype.", details={"field": "genotype"})
        return cls(
            id=student_id,
            tenant_id=tenant_id,
            blood_group=group,
            genotype=geno,
            allergies=_items(allergies, "allergies"),
            conditions=_items(conditions, "conditions"),
            medications=_items(medications, "medications"),
            notes=_note(notes, 4_000, "notes"),
            staff_alert=_note(staff_alert, 300, "staff_alert"),
            doctor=_note(doctor, 200, "doctor"),
            updated_by=by,
            updated_at=now,
        )

    @property
    def student_id(self) -> UUID:
        return self.id


@dataclass(frozen=True, kw_only=True)
class ClinicVisitClosed(DomainEvent):
    name: ClassVar[str] = "operations.clinic_visit_closed"
    visit_id: UUID
    student_id: UUID
    outcome: str
    urgent: bool


@dataclass(kw_only=True, eq=False)
class ClinicVisit(AggregateRoot):
    tenant_id: UUID
    student_id: UUID
    arrived_at: datetime
    complaint: str
    recorded_by: UUID
    assessment: str | None = None
    treatment: str | None = None
    temperature_dc: int | None = None  # tenths of a degree Celsius: 375 is 37.5 °C
    outcome: Outcome | None = None
    left_at: datetime | None = None
    medication_given: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def open(
        cls,
        *,
        tenant_id: UUID,
        student_id: UUID,
        complaint: str,
        arrived_at: datetime | None,
        by: UUID,
        now: datetime,
    ) -> ClinicVisit:
        text = _note(complaint, 1_000, "complaint")
        if text is None:
            raise ValidationFailed("Say why they came in.", details={"field": "complaint"})
        when = arrived_at or now
        if when > now + timedelta(minutes=5) or when < now - timedelta(days=7):
            raise ValidationFailed(
                "Arrival must be within the last week.", details={"field": "arrived_at"}
            )
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            student_id=student_id,
            arrived_at=when,
            complaint=text,
            recorded_by=by,
        )

    @property
    def is_open(self) -> bool:
        return self.outcome is None

    def update(
        self,
        *,
        assessment: str | None,
        treatment: str | None,
        temperature_dc: int | None,
        medication_given: list[str],
    ) -> None:
        if not self.is_open:
            raise InvalidStateTransition("This visit is closed.", code="visit_closed")
        if temperature_dc is not None and not 300 <= temperature_dc <= 450:
            raise ValidationFailed(
                "Enter the temperature in tenths of a degree (e.g. 375 for 37.5 °C).",
                details={"field": "temperature_dc"},
            )
        self.assessment = _note(assessment, 4_000, "assessment")
        self.treatment = _note(treatment, 4_000, "treatment")
        self.temperature_dc = temperature_dc
        self.medication_given = _items(medication_given, "medication_given")

    def close(self, *, outcome: Outcome, now: datetime) -> None:
        if not self.is_open:
            raise InvalidStateTransition("This visit is closed.", code="visit_closed")
        self.outcome = outcome
        self.left_at = now
        self.record(
            ClinicVisitClosed(
                tenant_id=self.tenant_id,
                visit_id=self.id,
                student_id=self.student_id,
                outcome=outcome.value,
                urgent=outcome in URGENT_OUTCOMES,
            )
        )
