"""The clinic: health records and visits to the nurse.

- The nurse (``clinic:manage``) keeps each student's record and logs every visit.
- Guardians see and update their own child's record and see their child's visits. Students see
  their own.
- Other staff with ``clinic:read`` see only the medical alert: allergies and the note the school
  chose to share.
- Parents are told in the app when a visit ends; when a child is sent home, collected or sent to
  hospital they are emailed too.
- Record contents and visit notes are encrypted; the activity log names the fields that changed,
  never their values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from app.domain.academics.sections import SectionFeature
from app.domain.communication.inbox import Notification, NotificationKind
from app.domain.identity.roles import Realm, SchoolRole
from app.domain.operations.clinic import ClinicVisit, HealthRecord, Outcome
from app.domain.operations.repositories import StoredHealthRecord, StoredVisit
from app.domain.shared.errors import NotFound, PermissionDenied, ValidationFailed
from app.domain.shared.value_objects import Page
from app.use_cases.academics.sections import (
    ensure_student_in_scope,
    serve_student,
    visible_students,
)
from app.use_cases.identity.guardians import is_guardian_or_self
from app.use_cases.shared import audit
from app.use_cases.shared.pagination import clamp_limit
from app.use_cases.shared.ports import Clock, Scope, SecretBox, UnitOfWork, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal

_RECORD_FIELDS = (
    "blood_group",
    "genotype",
    "allergies",
    "conditions",
    "medications",
    "notes",
    "staff_alert",
    "doctor",
)
_LABELS = {
    Outcome.RETURNED_TO_CLASS: "returned to class",
    Outcome.RESTED_IN_CLINIC: "rested in the clinic",
    Outcome.SENT_HOME: "was sent home",
    Outcome.COLLECTED_BY_PARENT: "was collected",
    Outcome.REFERRED_TO_HOSPITAL: "was referred to hospital",
}


@dataclass(frozen=True, slots=True)
class RecordView:
    student_id: UUID
    blood_group: str | None
    genotype: str | None
    allergies: list[str]
    conditions: list[str]
    medications: list[str]
    notes: str | None
    staff_alert: str | None
    doctor: str | None
    updated_by: UUID | None
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class AlertView:
    student_id: UUID
    student_name: str
    allergies: list[str]
    staff_alert: str | None


@dataclass(frozen=True, slots=True)
class VisitView:
    id: UUID
    student_id: UUID
    student_name: str
    arrived_at: datetime
    complaint: str
    assessment: str | None
    treatment: str | None
    temperature_dc: int | None
    medication_given: list[str]
    outcome: str | None
    left_at: datetime | None
    recorded_by: UUID


@dataclass(frozen=True, slots=True)
class RecordCommand:
    blood_group: str | None = None
    genotype: str | None = None
    allergies: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    medications: tuple[str, ...] = ()
    notes: str | None = None
    staff_alert: str | None = None
    doctor: str | None = None


@dataclass(frozen=True, slots=True)
class VisitNotes:
    assessment: str | None = None
    treatment: str | None = None
    temperature_dc: int | None = None
    medication_given: tuple[str, ...] = ()


class Clinic:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock, box: SecretBox) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._box = box

    # ---- health records ---------------------------------------------------------------------

    async def record(self, actor: Principal, student_id: UUID) -> RecordView:
        """The full record, for the nurse, the student's guardians and the student."""
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await _student(uow, tenant_id, student_id)
            family = await is_guardian_or_self(uow, actor, student_id)
            if not family:
                actor.require("clinic:manage")
                await ensure_student_in_scope(uow, actor, student_id)
            stored = await uow.clinic.get_record(student_id)
            if not family:
                uow.audit(
                    audit.by(
                        actor,
                        "clinic.record_viewed",
                        target_type="school_user",
                        target_id=student_id,
                    )
                )
            await uow.commit()
        return self._view(student_id, stored)

    async def save_record(
        self, actor: Principal, student_id: UUID, cmd: RecordCommand
    ) -> RecordView:
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await _student(uow, tenant_id, student_id)
            if not (
                SchoolRole.PARENT.value in actor.roles
                and await uow.guardians.is_guardian(actor.user_id, student_id)
            ):
                actor.require("clinic:manage")
                await ensure_student_in_scope(uow, actor, student_id)
            before = self._view(student_id, await uow.clinic.get_record(student_id))
            record = HealthRecord.build(
                tenant_id=tenant_id,
                student_id=student_id,
                blood_group=cmd.blood_group,
                genotype=cmd.genotype,
                allergies=list(cmd.allergies),
                conditions=list(cmd.conditions),
                medications=list(cmd.medications),
                notes=cmd.notes,
                staff_alert=cmd.staff_alert,
                doctor=cmd.doctor,
                by=actor.user_id,
                now=now,
            )
            details = {
                "allergies": list(record.allergies),
                "conditions": list(record.conditions),
                "medications": list(record.medications),
                "notes": record.notes,
                "doctor": record.doctor,
            }
            alert = {"allergies": list(record.allergies), "staff_alert": record.staff_alert}
            stored = StoredHealthRecord(
                student_id=student_id,
                blood_group=record.blood_group,
                genotype=record.genotype,
                details_ciphertext=self._seal(details),
                alert_ciphertext=self._seal(alert),
                updated_by=actor.user_id,
                updated_at=now,
            )
            await uow.clinic.save_record(stored)
            after = self._view(student_id, stored)
            changed = sorted(k for k in _RECORD_FIELDS if getattr(before, k) != getattr(after, k))
            uow.audit(
                audit.by(
                    actor,
                    "clinic.record_updated",
                    target_type="school_user",
                    target_id=student_id,
                    fields=changed,
                )
            )
            await uow.commit()
        return after

    async def alert(self, actor: Principal, student_id: UUID) -> AlertView:
        """What a teacher needs to know: allergies and the shared note."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("clinic:read")
        if not actor.roles - {SchoolRole.STUDENT.value, SchoolRole.PARENT.value}:
            raise PermissionDenied("For staff.")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            student = await _student(uow, tenant_id, student_id)
            await ensure_student_in_scope(uow, actor, student_id)
            stored = await uow.clinic.get_record(student_id)
        data = self._open(stored.alert_ciphertext) if stored and stored.alert_ciphertext else {}
        return AlertView(
            student_id=student_id,
            student_name=student,
            allergies=list(data.get("allergies", [])),
            staff_alert=data.get("staff_alert"),
        )

    # ---- visits -----------------------------------------------------------------------------

    async def open_visit(
        self, actor: Principal, *, student_id: UUID, complaint: str, arrived_at: datetime | None
    ) -> VisitView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("clinic:manage")
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            name = await _student(uow, tenant_id, student_id)
            await serve_student(uow, actor, student_id, SectionFeature.CLINIC)
            visit = ClinicVisit.open(
                tenant_id=tenant_id,
                student_id=student_id,
                complaint=complaint,
                arrived_at=arrived_at,
                by=actor.user_id,
                now=now,
            )
            await uow.clinic.add_visit(self._store(visit))
            uow.audit(
                audit.by(
                    actor,
                    "clinic.visit_opened",
                    target_type="clinic_visit",
                    target_id=visit.id,
                    student_id=str(student_id),
                )
            )
            await uow.commit()
        return _visit_view(visit, name)

    async def update_visit(self, actor: Principal, visit_id: UUID, notes: VisitNotes) -> VisitView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("clinic:manage")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            visit = await self._load(uow, tenant_id, visit_id, for_update=True)
            await ensure_student_in_scope(uow, actor, visit.student_id)
            visit.update(
                assessment=notes.assessment,
                treatment=notes.treatment,
                temperature_dc=notes.temperature_dc,
                medication_given=list(notes.medication_given),
            )
            await uow.clinic.save_visit(self._store(visit))
            uow.audit(
                audit.by(
                    actor,
                    "clinic.visit_updated",
                    target_type="clinic_visit",
                    target_id=visit.id,
                    medication_given=bool(visit.medication_given),
                )
            )
            name = await _student(uow, tenant_id, visit.student_id)
            await uow.commit()
        return _visit_view(visit, name)

    async def close_visit(self, actor: Principal, visit_id: UUID, *, outcome: str) -> VisitView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("clinic:manage")
        try:
            result = Outcome(outcome)
        except ValueError as exc:
            raise ValidationFailed("Unknown outcome.", details={"field": "outcome"}) from exc
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            visit = await self._load(uow, tenant_id, visit_id, for_update=True)
            await ensure_student_in_scope(uow, actor, visit.student_id)
            visit.close(outcome=result, now=now)
            await uow.clinic.save_visit(self._store(visit))
            uow.track(visit)
            name = await _student(uow, tenant_id, visit.student_id)
            guardians = await uow.guardians.guardians_of(visit.student_id)
            first = name.split()[0] if name else "Your child"
            await uow.communication.notify(
                [
                    Notification.new(
                        tenant_id=tenant_id,
                        user_id=g.parent_id,
                        kind=NotificationKind.GENERAL,
                        title=f"{first} visited the school clinic",
                        body=f"{first} {_LABELS[result]}.",
                        link_type="clinic_visit",
                        link_id=visit.id,
                        now=now,
                    )
                    for g in guardians
                ]
            )
            uow.audit(
                audit.by(
                    actor,
                    "clinic.visit_closed",
                    target_type="clinic_visit",
                    target_id=visit.id,
                    outcome=result.value,
                )
            )
            await uow.commit()
        return _visit_view(visit, name)

    async def visits(
        self,
        actor: Principal,
        *,
        student_id: UUID | None,
        open_only: bool,
        cursor: str | None,
        limit: int | None,
    ) -> Page[VisitView]:
        """The nurse sees all; a guardian or the student sees that student's visits."""
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            family = student_id is not None and await is_guardian_or_self(uow, actor, student_id)
            if not family:
                actor.require("clinic:manage")
                if student_id is not None:
                    await ensure_student_in_scope(uow, actor, student_id)
            since = None if student_id else self._clock.now() - timedelta(days=60)
            page = await uow.clinic.visits(
                student_id=student_id,
                open_only=open_only,
                since=since,
                cursor=cursor,
                limit=clamp_limit(limit),
            )
            names = await uow.users.school_names({v.student_id for v in page.items})
            shown = (
                {v.student_id for v in page.items}
                if family
                else await visible_students(uow, actor, {v.student_id for v in page.items})
            )
        return Page(
            items=[
                _visit_view(self._visit(v, tenant_id), names.get(v.student_id, ""))
                for v in page.items
                if v.student_id in shown
            ],
            next_cursor=page.next_cursor,
        )

    # ---- helpers ----------------------------------------------------------------------------

    async def _load(
        self, uow: UnitOfWork, tenant_id: UUID, visit_id: UUID, *, for_update: bool
    ) -> ClinicVisit:
        stored = await uow.clinic.get_visit(visit_id, for_update=for_update)
        if stored is None:
            raise NotFound("Visit not found.")
        return self._visit(stored, tenant_id)

    def _store(self, v: ClinicVisit) -> StoredVisit:
        return StoredVisit(
            id=v.id,
            student_id=v.student_id,
            arrived_at=v.arrived_at,
            temperature_dc=v.temperature_dc,
            outcome=v.outcome,
            left_at=v.left_at,
            recorded_by=v.recorded_by,
            notes_ciphertext=self._seal(
                {
                    "complaint": v.complaint,
                    "assessment": v.assessment,
                    "treatment": v.treatment,
                    "medication_given": list(v.medication_given),
                }
            ),
        )

    def _visit(self, s: StoredVisit, tenant_id: UUID) -> ClinicVisit:
        notes = self._open(s.notes_ciphertext)
        return ClinicVisit(
            id=s.id,
            tenant_id=tenant_id,
            student_id=s.student_id,
            arrived_at=s.arrived_at,
            complaint=str(notes.get("complaint", "")),
            recorded_by=s.recorded_by,
            assessment=notes.get("assessment"),
            treatment=notes.get("treatment"),
            temperature_dc=s.temperature_dc,
            outcome=s.outcome,
            left_at=s.left_at,
            medication_given=tuple(notes.get("medication_given", [])),
        )

    def _view(self, student_id: UUID, stored: StoredHealthRecord | None) -> RecordView:
        if stored is None:
            return RecordView(
                student_id=student_id,
                blood_group=None,
                genotype=None,
                allergies=[],
                conditions=[],
                medications=[],
                notes=None,
                staff_alert=None,
                doctor=None,
                updated_by=None,
                updated_at=None,
            )
        details = self._open(stored.details_ciphertext)
        alert = self._open(stored.alert_ciphertext) if stored.alert_ciphertext else {}
        return RecordView(
            student_id=student_id,
            blood_group=stored.blood_group,
            genotype=stored.genotype,
            allergies=list(details.get("allergies", [])),
            conditions=list(details.get("conditions", [])),
            medications=list(details.get("medications", [])),
            notes=details.get("notes"),
            staff_alert=alert.get("staff_alert"),
            doctor=details.get("doctor"),
            updated_by=stored.updated_by,
            updated_at=stored.updated_at,
        )

    def _seal(self, data: dict[str, Any]) -> str:
        return self._box.encrypt(json.dumps(data, separators=(",", ":")).encode())

    def _open(self, sealed: str) -> dict[str, Any]:
        value = json.loads(self._box.decrypt(sealed))
        return value if isinstance(value, dict) else {}


async def _student(uow: UnitOfWork, tenant_id: UUID, student_id: UUID) -> str:
    user = await uow.users.get(student_id)
    if user is None or user.tenant_id != tenant_id or SchoolRole.STUDENT.value not in user.roles:
        raise NotFound("Student not found.")
    return user.full_name


def _visit_view(v: ClinicVisit, name: str) -> VisitView:
    return VisitView(
        id=v.id,
        student_id=v.student_id,
        student_name=name,
        arrived_at=v.arrived_at,
        complaint=v.complaint,
        assessment=v.assessment,
        treatment=v.treatment,
        temperature_dc=v.temperature_dc,
        medication_given=list(v.medication_given),
        outcome=v.outcome.value if v.outcome else None,
        left_at=v.left_at,
        recorded_by=v.recorded_by,
    )
