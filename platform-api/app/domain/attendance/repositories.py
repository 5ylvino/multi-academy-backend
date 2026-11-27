"""Attendance persistence contracts. Every method is scoped to the current school."""

from __future__ import annotations

from datetime import date
from typing import Protocol
from uuid import UUID

from app.domain.attendance.register import AttendanceSummary, StudentMark
from app.domain.attendance.staff import AttendanceSettings, StaffCheckIn


class AttendanceRepository(Protocol):
    async def get_settings(self) -> AttendanceSettings | None: ...

    async def save_settings(self, settings: AttendanceSettings) -> None:
        """Insert or update; raises Conflict if someone saved a newer version first."""
        ...

    async def marks_for_class_day(self, class_id: UUID, day: date) -> list[StudentMark]: ...

    async def add_marks(self, marks: list[StudentMark]) -> list[UUID]:
        """Insert new marks; returns the student ids that already had a mark (skipped)."""
        ...

    async def get_mark(self, class_id: UUID, student_id: UUID, day: date) -> StudentMark | None: ...

    async def save_mark(self, mark: StudentMark) -> None: ...

    async def student_summary(
        self, student_id: UUID, start: date, end: date
    ) -> AttendanceSummary: ...

    async def get_check_in(self, staff_id: UUID, day: date) -> StaffCheckIn | None: ...

    async def add_check_in(self, check_in: StaffCheckIn) -> bool:
        """False if this person already checked in that day."""
        ...

    async def check_ins_for_day(self, day: date) -> list[StaffCheckIn]: ...

    async def check_ins_for_staff(
        self, staff_id: UUID, start: date, end: date
    ) -> list[StaffCheckIn]: ...
