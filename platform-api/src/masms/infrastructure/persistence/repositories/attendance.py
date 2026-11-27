"""Attendance repository. Every query is filtered by the scoped tenant as well as by RLS."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from masms.domain.attendance.register import AttendanceStatus, AttendanceSummary, StudentMark
from masms.domain.attendance.staff import AttendanceSettings, CheckInStatus, Site, StaffCheckIn
from masms.domain.shared.errors import Conflict
from masms.infrastructure.persistence.models.attendance import (
    AttendanceSettingsRow,
    StaffCheckInRow,
    StudentMarkRow,
)
from masms.infrastructure.persistence.repositories.scope_state import ScopeState

_STALE = "These settings were changed by someone else. Reload and try again."


def _mark(row: StudentMarkRow) -> StudentMark:
    return StudentMark(
        id=row.id,
        tenant_id=row.tenant_id,
        class_id=row.class_id,
        session_id=row.session_id,
        student_id=row.student_id,
        day=row.day,
        status=AttendanceStatus(row.status),
        marked_by=row.marked_by,
        marked_at=row.marked_at,
        note=row.note,
        corrected_by=row.corrected_by,
        corrected_at=row.corrected_at,
        correction_reason=row.correction_reason,
    )


def _check_in(row: StaffCheckInRow) -> StaffCheckIn:
    return StaffCheckIn(
        id=row.id,
        tenant_id=row.tenant_id,
        staff_id=row.staff_id,
        day=row.day,
        status=CheckInStatus(row.status),
        checked_in_at=row.checked_in_at,
        site_name=row.site_name,
    )


class SqlAttendanceRepository:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    @property
    def _tid(self) -> UUID:
        if self._scope.tenant_id is None:
            raise RuntimeError("Attendance data needs a tenant scope.")
        return self._scope.tenant_id

    # ---- settings ------------------------------------------------------------------------

    async def get_settings(self) -> AttendanceSettings | None:
        row = await self._s.get(AttendanceSettingsRow, self._tid)
        if row is None:
            return None
        return AttendanceSettings(
            id=row.tenant_id,
            tenant_id=row.tenant_id,
            window_opens=row.window_opens,
            late_after=row.late_after,
            window_closes=row.window_closes,
            business_days=frozenset(row.business_days),
            check_in_roles=frozenset(row.check_in_roles),
            sites=tuple(
                Site(
                    name=str(s["name"]),
                    latitude=float(s["latitude"]),
                    longitude=float(s["longitude"]),
                    radius_m=int(s["radius_m"]),
                )
                for s in row.sites
            ),
            require_location=row.require_location,
            kiosk_secret_ciphertext=row.kiosk_secret_ciphertext,
            version=row.version,
        )

    async def save_settings(self, settings: AttendanceSettings) -> None:
        values = {
            "window_opens": settings.window_opens,
            "late_after": settings.late_after,
            "window_closes": settings.window_closes,
            "business_days": sorted(settings.business_days),
            "check_in_roles": sorted(settings.check_in_roles),
            "sites": [
                {
                    "name": s.name,
                    "latitude": s.latitude,
                    "longitude": s.longitude,
                    "radius_m": s.radius_m,
                }
                for s in settings.sites
            ],
            "require_location": settings.require_location,
            "kiosk_secret_ciphertext": settings.kiosk_secret_ciphertext,
            "version": settings.version + 1,
        }
        insert = pg_insert(AttendanceSettingsRow).values(tenant_id=self._tid, **values)
        stmt = insert.on_conflict_do_update(
            index_elements=["tenant_id"],
            set_={**values, "updated_at": datetime.now(UTC)},
            where=AttendanceSettingsRow.version == settings.version,
        ).returning(AttendanceSettingsRow.tenant_id)
        if (await self._s.execute(stmt)).first() is None:
            raise Conflict(_STALE)
        settings.version += 1

    # ---- student marks -------------------------------------------------------------------

    async def marks_for_class_day(self, class_id: UUID, day: date) -> list[StudentMark]:
        rows = await self._s.scalars(
            select(StudentMarkRow).where(
                StudentMarkRow.tenant_id == self._tid,
                StudentMarkRow.class_id == class_id,
                StudentMarkRow.day == day,
            )
        )
        return [_mark(r) for r in rows]

    async def add_marks(self, marks: list[StudentMark]) -> list[UUID]:
        if not marks:
            return []
        stmt = (
            pg_insert(StudentMarkRow)
            .values(
                [
                    {
                        "tenant_id": self._tid,
                        "day": m.day,
                        "id": m.id,
                        "class_id": m.class_id,
                        "session_id": m.session_id,
                        "student_id": m.student_id,
                        "status": m.status.value,
                        "marked_by": m.marked_by,
                        "marked_at": m.marked_at,
                        "note": m.note,
                    }
                    for m in marks
                ]
            )
            .on_conflict_do_nothing(constraint="uq_attendance_student_marks_student_day")
            .returning(StudentMarkRow.student_id)
        )
        inserted = set((await self._s.scalars(stmt)).all())
        return [m.student_id for m in marks if m.student_id not in inserted]

    async def get_mark(self, class_id: UUID, student_id: UUID, day: date) -> StudentMark | None:
        row = await self._s.scalar(
            select(StudentMarkRow)
            .where(
                StudentMarkRow.tenant_id == self._tid,
                StudentMarkRow.class_id == class_id,
                StudentMarkRow.student_id == student_id,
                StudentMarkRow.day == day,
            )
            .with_for_update()
        )
        return _mark(row) if row else None

    async def save_mark(self, mark: StudentMark) -> None:
        await self._s.execute(
            update(StudentMarkRow)
            .where(
                StudentMarkRow.tenant_id == self._tid,
                StudentMarkRow.day == mark.day,
                StudentMarkRow.id == mark.id,
            )
            .values(
                status=mark.status.value,
                corrected_by=mark.corrected_by,
                corrected_at=mark.corrected_at,
                correction_reason=mark.correction_reason,
            )
            .execution_options(synchronize_session=False)
        )

    async def student_summary(self, student_id: UUID, start: date, end: date) -> AttendanceSummary:
        rows = await self._s.execute(
            select(StudentMarkRow.status, func.count())
            .where(
                StudentMarkRow.tenant_id == self._tid,
                StudentMarkRow.student_id == student_id,
                StudentMarkRow.day.between(start, end),
            )
            .group_by(StudentMarkRow.status)
        )
        counts = {str(status): int(n) for status, n in rows.all()}
        return AttendanceSummary(
            present=counts.get("present", 0),
            late=counts.get("late", 0),
            absent=counts.get("absent", 0),
            excused=counts.get("excused", 0),
        )

    # ---- staff check-ins -----------------------------------------------------------------

    async def get_check_in(self, staff_id: UUID, day: date) -> StaffCheckIn | None:
        row = await self._s.scalar(
            select(StaffCheckInRow).where(
                StaffCheckInRow.tenant_id == self._tid,
                StaffCheckInRow.staff_id == staff_id,
                StaffCheckInRow.day == day,
            )
        )
        return _check_in(row) if row else None

    async def add_check_in(self, check_in: StaffCheckIn) -> bool:
        stmt = (
            pg_insert(StaffCheckInRow)
            .values(
                tenant_id=self._tid,
                day=check_in.day,
                id=check_in.id,
                staff_id=check_in.staff_id,
                status=check_in.status.value,
                checked_in_at=check_in.checked_in_at,
                site_name=check_in.site_name,
            )
            .on_conflict_do_nothing(constraint="uq_attendance_staff_check_ins_staff_day")
            .returning(StaffCheckInRow.id)
        )
        return (await self._s.execute(stmt)).first() is not None

    async def check_ins_for_day(self, day: date) -> list[StaffCheckIn]:
        rows = await self._s.scalars(
            select(StaffCheckInRow).where(
                StaffCheckInRow.tenant_id == self._tid, StaffCheckInRow.day == day
            )
        )
        return [_check_in(r) for r in rows]

    async def check_ins_for_staff(
        self, staff_id: UUID, start: date, end: date
    ) -> list[StaffCheckIn]:
        rows = await self._s.scalars(
            select(StaffCheckInRow).where(
                StaffCheckInRow.tenant_id == self._tid,
                StaffCheckInRow.staff_id == staff_id,
                StaffCheckInRow.day.between(start, end),
            )
        )
        return [_check_in(r) for r in rows]
