"""Daily student registers.

A mark is written once. Changing it later is a correction: it needs a reason, keeps who
changed it and when, and is audited. That keeps the register trustworthy as a legal record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from app.domain.shared.entity import Entity
from app.domain.shared.errors import ValidationFailed
from app.domain.shared.ids import new_id

MIN_REASON = 3
MAX_REASON = 500


class AttendanceStatus(StrEnum):
    PRESENT = "present"
    LATE = "late"
    ABSENT = "absent"
    EXCUSED = "excused"  # absent with a reason the school accepts (illness, approved leave)


def parse_status(value: str) -> AttendanceStatus:
    try:
        return AttendanceStatus(value)
    except ValueError as exc:
        raise ValidationFailed(
            "Status must be present, late, absent or excused.", details={"field": "status"}
        ) from exc


def ensure_markable_day(day: date, *, today: date, term_start: date, term_end: date) -> None:
    """Registers are for school days in the current term, and never for the future."""
    if day > today:
        raise ValidationFailed("You can't mark a future date.", details={"field": "date"})
    if not term_start <= day <= term_end:
        raise ValidationFailed("That date is outside the current term.", details={"field": "date"})


@dataclass(kw_only=True, eq=False)
class StudentMark(Entity):
    tenant_id: UUID
    class_id: UUID
    session_id: UUID
    student_id: UUID
    day: date
    status: AttendanceStatus
    marked_by: UUID
    marked_at: datetime
    note: str | None = None
    corrected_by: UUID | None = None
    corrected_at: datetime | None = None
    correction_reason: str | None = None

    @classmethod
    def record(
        cls,
        *,
        tenant_id: UUID,
        class_id: UUID,
        session_id: UUID,
        student_id: UUID,
        day: date,
        status: AttendanceStatus,
        marked_by: UUID,
        now: datetime,
        note: str | None = None,
    ) -> StudentMark:
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            class_id=class_id,
            session_id=session_id,
            student_id=student_id,
            day=day,
            status=status,
            marked_by=marked_by,
            marked_at=now,
            note=(note or "").strip()[:300] or None,
        )

    def correct(
        self, *, status: AttendanceStatus, reason: str, by: UUID, now: datetime
    ) -> AttendanceStatus:
        """Change the mark; returns the previous status for the audit trail."""
        clean = " ".join(reason.split())
        if not MIN_REASON <= len(clean) <= MAX_REASON:
            raise ValidationFailed("Give a reason for the correction.", details={"field": "reason"})
        if status is self.status:
            raise ValidationFailed(
                "The register already shows that status.", details={"field": "status"}
            )
        previous = self.status
        self.status = status
        self.corrected_by = by
        self.corrected_at = now
        self.correction_reason = clean
        return previous


@dataclass(frozen=True, slots=True)
class AttendanceSummary:
    present: int
    late: int
    absent: int
    excused: int

    @property
    def total(self) -> int:
        return self.present + self.late + self.absent + self.excused

    @property
    def attendance_rate(self) -> float | None:
        """Share of marked days the student was in school (late counts as attended)."""
        if self.total == 0:
            return None
        return round((self.present + self.late) / self.total, 4)
