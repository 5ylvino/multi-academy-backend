"""Student registers: who may take them, the daily register, corrections and summaries.

Access: anyone with ``student_attendance:view_all`` covers the whole school. Everyone else
needs a role permission and must teach the class (homeroom or one of its subjects).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from app.domain.academics.sections import SectionFeature
from app.domain.attendance.register import (
    AttendanceStatus,
    StudentMark,
    ensure_markable_day,
    parse_status,
)
from app.domain.identity.roles import Realm, SchoolRole
from app.domain.shared.errors import NotFound, PermissionDenied, ValidationFailed
from app.use_cases.academics.sections import require_feature, scope_of
from app.use_cases.shared import audit
from app.use_cases.shared.ports import (
    AuditEntry,
    Clock,
    SchoolTime,
    Scope,
    UnitOfWork,
    UnitOfWorkFactory,
)
from app.use_cases.shared.principal import Principal

MAX_ENTRIES = 1000
MAX_SUMMARY_DAYS = 400


@dataclass(frozen=True, slots=True)
class RegisterEntry:
    student_id: UUID
    full_name: str
    status: str | None  # None = not marked yet
    marked_at: datetime | None
    corrected: bool
    note: str | None


@dataclass(frozen=True, slots=True)
class RegisterView:
    class_id: UUID
    day: date
    session_id: UUID
    complete: bool
    entries: list[RegisterEntry]


@dataclass(frozen=True, slots=True)
class MarkInput:
    student_id: UUID
    status: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class SubmitResult:
    recorded: int
    already_marked: list[UUID]


@dataclass(frozen=True, slots=True)
class SummaryView:
    student_id: UUID
    start: date
    end: date
    present: int
    late: int
    absent: int
    excused: int
    attendance_rate: float | None


async def _may_take(uow: UnitOfWork, actor: Principal, class_id: UUID) -> None:
    school_class = await uow.academics.get_class(class_id)
    if school_class is None:
        raise NotFound("Class not found.")
    await require_feature(uow, school_class.school_level, SectionFeature.ATTENDANCE)
    if await uow.academics.teaches_class(actor.user_id, class_id):
        return
    if actor.permissions.allows("student_attendance:view_all"):
        (await scope_of(uow, actor)).require(school_class.school_level)
        return
    raise PermissionDenied("You can only take the register for classes you teach.")


class StudentRegisters:
    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, clock: Clock, school_time: SchoolTime
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._school_time = school_time

    async def _today(self, tenant_id: UUID) -> date:
        return (await self._school_time.now_local(tenant_id)).date()

    async def register(self, actor: Principal, class_id: UUID, day: date | None) -> RegisterView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("student_attendance:read")
        tenant_id = actor.school_tenant_id
        day = day or await self._today(tenant_id)
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            school_class = await uow.academics.get_class(class_id)
            if school_class is None:
                raise NotFound("Class not found.")
            await _may_take(uow, actor, class_id)
            term = await uow.academics.current_term()
            if term is None:
                raise ValidationFailed("Set the current term first.", code="no_current_term")
            roster = await uow.academics.roster(class_id, term.session_id)
            marks = {
                m.student_id: m for m in await uow.attendance.marks_for_class_day(class_id, day)
            }
            people = {
                u.id: u
                for u in await uow.users.school_users_with_roles(
                    tenant_id, frozenset({SchoolRole.STUDENT.value})
                )
            }
        entries = []
        for enrolment in roster:
            mark = marks.get(enrolment.student_id)
            person = people.get(enrolment.student_id)
            entries.append(
                RegisterEntry(
                    student_id=enrolment.student_id,
                    full_name=person.full_name if person else "",
                    status=mark.status.value if mark else None,
                    marked_at=mark.marked_at if mark else None,
                    corrected=bool(mark and mark.corrected_at),
                    note=mark.note if mark else None,
                )
            )
        entries.sort(key=lambda e: (e.full_name.lower(), str(e.student_id)))
        return RegisterView(
            class_id=class_id,
            day=day,
            session_id=term.session_id,
            complete=bool(entries) and all(e.status is not None for e in entries),
            entries=entries,
        )

    async def submit(
        self, actor: Principal, class_id: UUID, day: date | None, entries: list[MarkInput]
    ) -> SubmitResult:
        """Record marks. Already-marked students are reported back, never overwritten."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("student_attendance:create")
        if not entries:
            raise ValidationFailed("Mark at least one student.", details={"field": "entries"})
        if len(entries) > MAX_ENTRIES:
            raise ValidationFailed(
                "Too many entries in one register.", details={"field": "entries"}
            )
        ids = [e.student_id for e in entries]
        if len(set(ids)) != len(ids):
            raise ValidationFailed("A student appears twice.", details={"field": "entries"})
        statuses = [parse_status(e.status) for e in entries]

        tenant_id = actor.school_tenant_id
        today = await self._today(tenant_id)
        day = day or today
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            school_class = await uow.academics.get_class(class_id)
            if school_class is None or not school_class.active:
                raise NotFound("Class not found.")
            await _may_take(uow, actor, class_id)
            term = await uow.academics.current_term()
            if term is None:
                raise ValidationFailed("Set the current term first.", code="no_current_term")
            ensure_markable_day(day, today=today, term_start=term.starts_on, term_end=term.ends_on)
            enrolled = {e.student_id for e in await uow.academics.roster(class_id, term.session_id)}
            strangers = [str(i) for i in ids if i not in enrolled]
            if strangers:
                raise ValidationFailed(
                    "Some students aren't in this class.", details={"student_ids": strangers}
                )
            marks = [
                StudentMark.record(
                    tenant_id=tenant_id,
                    class_id=class_id,
                    session_id=term.session_id,
                    student_id=entry.student_id,
                    day=day,
                    status=status,
                    marked_by=actor.user_id,
                    now=now,
                    note=entry.note,
                )
                for entry, status in zip(entries, statuses, strict=True)
            ]
            skipped = await uow.attendance.add_marks(marks)
            counts: dict[str, int] = {}
            for mark in marks:
                if mark.student_id not in skipped:
                    counts[mark.status.value] = counts.get(mark.status.value, 0) + 1
            uow.audit(
                audit.by(
                    actor,
                    "attendance.register_submitted",
                    target_type="class",
                    target_id=class_id,
                    day=day.isoformat(),
                    recorded=len(marks) - len(skipped),
                    already_marked=len(skipped),
                    counts=counts,
                )
            )
            await uow.commit()
        return SubmitResult(recorded=len(marks) - len(skipped), already_marked=skipped)

    async def correct(
        self,
        actor: Principal,
        class_id: UUID,
        *,
        day: date,
        student_id: UUID,
        status: str,
        reason: str,
    ) -> RegisterEntry:
        actor.require_realm(Realm.SCHOOL)
        actor.require("student_attendance:update")
        new_status = parse_status(status)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await _may_take(uow, actor, class_id)
            mark = await uow.attendance.get_mark(class_id, student_id, day)
            if mark is None:
                raise NotFound("That student has no mark for this day yet.")
            previous = mark.correct(
                status=new_status, reason=reason, by=actor.user_id, now=self._clock.now()
            )
            await uow.attendance.save_mark(mark)
            uow.audit(
                AuditEntry(
                    action="attendance.mark_corrected",
                    actor_id=actor.user_id,
                    actor_realm=Realm.SCHOOL,
                    tenant_id=tenant_id,
                    target_type="attendance_mark",
                    target_id=str(mark.id),
                    metadata={
                        "student_id": str(student_id),
                        "day": day.isoformat(),
                        "from": previous.value,
                        "to": new_status.value,
                        "reason": mark.correction_reason,
                    },
                )
            )
            await uow.commit()
        return RegisterEntry(
            student_id=student_id,
            full_name="",
            status=mark.status.value,
            marked_at=mark.marked_at,
            corrected=True,
            note=mark.note,
        )

    async def summary(
        self, actor: Principal, student_id: UUID, *, start: date | None, end: date | None
    ) -> SummaryView:
        """Defaults to the current term. Students may see their own."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("student_attendance:read")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            term = await uow.academics.current_term()
            if start is None or end is None:
                if term is None:
                    raise ValidationFailed("Set the current term first.", code="no_current_term")
                start, end = start or term.starts_on, end or term.ends_on
            if end < start or end - start > timedelta(days=MAX_SUMMARY_DAYS):
                raise ValidationFailed(
                    "Pick a range of up to 400 days.", details={"field": "range"}
                )
            if actor.user_id != student_id:
                class_id = (
                    await uow.academics.class_of_student(student_id, term.session_id)
                    if term
                    else None
                )
                teaches = class_id is not None and await uow.academics.teaches_class(
                    actor.user_id, class_id
                )
                if not teaches:
                    if not actor.permissions.allows("student_attendance:view_all"):
                        raise PermissionDenied("You can only see students you teach.")
                    scope = await scope_of(uow, actor)
                    school_class = await uow.academics.get_class(class_id) if class_id else None
                    if not scope.whole_school and (
                        school_class is None or not scope.covers(school_class.school_level)
                    ):
                        raise PermissionDenied(
                            "This student is in another section of the school.",
                            code="other_section",
                        )
            totals = await uow.attendance.student_summary(student_id, start, end)
        return SummaryView(
            student_id=student_id,
            start=start,
            end=end,
            present=totals.present,
            late=totals.late,
            absent=totals.absent,
            excused=totals.excused,
            attendance_rate=totals.attendance_rate,
        )


__all__ = ["AttendanceStatus", "MarkInput", "StudentRegisters"]
