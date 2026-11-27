"""Set up the academic calendar, classes and subjects, and enrol students."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from masms.domain.academics.structure import (
    AcademicSession,
    Enrolment,
    EnrolmentStatus,
    SchoolClass,
    SchoolLevelCode,
    Subject,
    Term,
    TermCode,
    normalise_code,
)
from masms.domain.identity.roles import Realm, SchoolRole
from masms.domain.shared.errors import Conflict, NotFound, ValidationFailed
from masms.use_cases.shared.ports import AuditEntry, Clock, Scope, UnitOfWork, UnitOfWorkFactory
from masms.use_cases.shared.principal import Principal


def _level(value: str) -> SchoolLevelCode:
    try:
        return SchoolLevelCode(value)
    except ValueError as exc:
        raise ValidationFailed("Unknown school level.", details={"field": "school_level"}) from exc


async def _ensure_level_offered(uow: UnitOfWork, tenant_id: UUID, level: SchoolLevelCode) -> None:
    tenant = await uow.tenants.get(tenant_id)
    if tenant is None or level.value not in {lvl.value for lvl in tenant.school_levels}:
        raise ValidationFailed(
            "Your school doesn't offer this level.", details={"field": "school_level"}
        )


# ---- views -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TermView:
    id: UUID
    session_id: UUID
    code: str
    name: str
    starts_on: date
    ends_on: date
    is_current: bool

    @classmethod
    def of(cls, t: Term) -> TermView:
        return cls(t.id, t.session_id, t.code.value, t.name, t.starts_on, t.ends_on, t.is_current)


@dataclass(frozen=True, slots=True)
class SessionView:
    id: UUID
    name: str
    starts_on: date
    ends_on: date
    is_current: bool
    terms: list[TermView]

    @classmethod
    def of(cls, s: AcademicSession, terms: list[Term]) -> SessionView:
        ordered = sorted(terms, key=lambda t: t.starts_on)
        return cls(
            s.id, s.name, s.starts_on, s.ends_on, s.is_current, [TermView.of(t) for t in ordered]
        )


@dataclass(frozen=True, slots=True)
class ClassView:
    id: UUID
    name: str
    code: str
    school_level: str
    class_teacher_id: UUID | None
    capacity: int | None
    active: bool
    subject_ids: list[UUID]

    @classmethod
    def of(cls, c: SchoolClass) -> ClassView:
        return cls(
            c.id,
            c.name,
            c.code,
            c.school_level.value,
            c.class_teacher_id,
            c.capacity,
            c.active,
            sorted(c.subject_ids),
        )


@dataclass(frozen=True, slots=True)
class SubjectView:
    id: UUID
    name: str
    code: str
    school_level: str
    description: str
    active: bool

    @classmethod
    def of(cls, s: Subject) -> SubjectView:
        return cls(s.id, s.name, s.code, s.school_level.value, s.description, s.active)


@dataclass(frozen=True, slots=True)
class EnrolmentView:
    id: UUID
    student_id: UUID
    class_id: UUID
    session_id: UUID
    status: str
    enrolled_at: datetime

    @classmethod
    def of(cls, e: Enrolment) -> EnrolmentView:
        return cls(e.id, e.student_id, e.class_id, e.session_id, e.status.value, e.enrolled_at)


# ---- calendar ----------------------------------------------------------------------------


class AcademicCalendar:
    """Sessions and terms. Small, so one class keeps the related actions together."""

    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def create_session(
        self, actor: Principal, *, name: str, starts_on: date, ends_on: date
    ) -> SessionView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("academic_sessions:manage")
        tenant_id = actor.school_tenant_id
        session = AcademicSession.create(
            tenant_id=tenant_id,
            name=name,
            starts_on=starts_on,
            ends_on=ends_on,
            now=self._clock.now(),
        )
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            for existing in await uow.academics.list_sessions():
                if existing.name.lower() == session.name.lower():
                    raise Conflict(
                        "A session with this name already exists.", details={"field": "name"}
                    )
                if existing.starts_on < session.ends_on and session.starts_on < existing.ends_on:
                    raise Conflict("Sessions can't overlap.", details={"overlaps": existing.name})
            await uow.academics.add_session(session)
            await uow.commit()
        return SessionView.of(session, [])

    async def add_term(
        self,
        actor: Principal,
        *,
        session_id: UUID,
        code: str,
        name: str | None,
        starts_on: date,
        ends_on: date,
    ) -> TermView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("academic_terms:manage")
        try:
            term_code = TermCode(code)
        except ValueError as exc:
            raise ValidationFailed(
                "Term must be first, second or third.", details={"field": "code"}
            ) from exc
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            session = await uow.academics.get_session(session_id)
            if session is None:
                raise NotFound("Session not found.")
            term = Term.create(
                session=session, code=term_code, name=name, starts_on=starts_on, ends_on=ends_on
            )
            for other in await uow.academics.list_terms(session.id):
                if other.code is term.code:
                    raise Conflict("This session already has that term.", details={"field": "code"})
                if other.starts_on < term.ends_on and term.starts_on < other.ends_on:
                    raise Conflict("Terms can't overlap.", details={"overlaps": other.name})
            await uow.academics.add_term(term)
            await uow.commit()
        return TermView.of(term)

    async def set_current_term(self, actor: Principal, term_id: UUID) -> TermView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("academic_terms:manage")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            term = await uow.academics.get_term(term_id)
            if term is None:
                raise NotFound("Term not found.")
            await uow.academics.make_current(term)
            term.is_current = True
            uow.audit(
                AuditEntry(
                    action="academics.current_term_set",
                    actor_id=actor.user_id,
                    actor_realm=Realm.SCHOOL,
                    tenant_id=tenant_id,
                    target_type="term",
                    target_id=str(term.id),
                    metadata={"session_id": str(term.session_id), "code": term.code.value},
                )
            )
            await uow.commit()
        return TermView.of(term)

    async def list_sessions(self, actor: Principal) -> list[SessionView]:
        actor.require_realm(Realm.SCHOOL)
        actor.require("dashboards:view")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            sessions = await uow.academics.list_sessions()
            views = [SessionView.of(s, await uow.academics.list_terms(s.id)) for s in sessions]
        return sorted(views, key=lambda v: v.starts_on, reverse=True)


# ---- classes and subjects ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateClassCommand:
    name: str
    code: str
    school_level: str
    capacity: int | None
    class_teacher_id: UUID | None


class ClassesAndSubjects:
    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def _ensure_teacher(self, uow: UnitOfWork, teacher_id: UUID | None) -> None:
        if teacher_id is None:
            return
        teacher = await uow.users.get(teacher_id)
        teaching = {
            SchoolRole.CLASS_TEACHER.value,
            SchoolRole.SUBJECT_TEACHER.value,
            SchoolRole.HEAD_TEACHER.value,
        }
        if teacher is None or not (teacher.roles & teaching):
            raise ValidationFailed(
                "Pick a teacher from your school.", details={"field": "class_teacher_id"}
            )

    async def create_class(self, actor: Principal, cmd: CreateClassCommand) -> ClassView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("classes:create")
        tenant_id = actor.school_tenant_id
        level = _level(cmd.school_level)
        school_class = SchoolClass.create(
            tenant_id=tenant_id,
            name=cmd.name,
            code=cmd.code,
            school_level=level,
            capacity=cmd.capacity,
            class_teacher_id=cmd.class_teacher_id,
        )
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await _ensure_level_offered(uow, tenant_id, level)
            if await uow.academics.class_code_exists(school_class.code):
                raise Conflict("A class with this code already exists.", details={"field": "code"})
            await self._ensure_teacher(uow, cmd.class_teacher_id)
            await uow.academics.add_class(school_class)
            await uow.commit()
        return ClassView.of(school_class)

    async def update_class(
        self,
        actor: Principal,
        class_id: UUID,
        *,
        name: str | None,
        capacity: int | None,
        class_teacher_id: UUID | None,
        active: bool | None,
        clear_teacher: bool = False,
    ) -> ClassView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("classes:update")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            school_class = await uow.academics.get_class(class_id)
            if school_class is None:
                raise NotFound("Class not found.")
            if name is not None:
                school_class.rename(name)
            if capacity is not None:
                school_class.set_capacity(capacity)
            if clear_teacher:
                school_class.class_teacher_id = None
            elif class_teacher_id is not None:
                await self._ensure_teacher(uow, class_teacher_id)
                school_class.class_teacher_id = class_teacher_id
            if active is not None:
                school_class.active = active
            await uow.academics.save_class(school_class)
            await uow.commit()
        return ClassView.of(school_class)

    async def list_classes(
        self, actor: Principal, *, level: str | None, include_inactive: bool
    ) -> list[ClassView]:
        actor.require_realm(Realm.SCHOOL)
        actor.require("classes:read")
        if level is not None:
            _level(level)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            classes = await uow.academics.list_classes(
                level=level, include_inactive=include_inactive
            )
        return [
            ClassView.of(c) for c in sorted(classes, key=lambda c: (c.school_level.value, c.code))
        ]

    async def create_subject(
        self, actor: Principal, *, name: str, code: str, school_level: str, description: str
    ) -> SubjectView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("subjects:create")
        tenant_id = actor.school_tenant_id
        level = _level(school_level)
        subject = Subject.create(
            tenant_id=tenant_id, name=name, code=code, school_level=level, description=description
        )
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await _ensure_level_offered(uow, tenant_id, level)
            if await uow.academics.subject_code_exists(subject.code, level.value):
                raise Conflict(
                    "A subject with this code already exists for this level.",
                    details={"field": "code"},
                )
            await uow.academics.add_subject(subject)
            await uow.commit()
        return SubjectView.of(subject)

    async def list_subjects(self, actor: Principal, *, level: str | None) -> list[SubjectView]:
        actor.require_realm(Realm.SCHOOL)
        actor.require("subjects:read")
        if level is not None:
            _level(level)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            subjects = await uow.academics.list_subjects(level=level)
        return [
            SubjectView.of(s)
            for s in sorted(subjects, key=lambda s: (s.school_level.value, s.code))
        ]

    async def link_subject(
        self,
        actor: Principal,
        *,
        class_id: UUID,
        subject_id: UUID,
        teacher_id: UUID | None,
        linked: bool,
    ) -> ClassView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("classes:update")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            school_class = await uow.academics.get_class(class_id)
            subject = await uow.academics.get_subject(subject_id)
            if school_class is None or subject is None:
                raise NotFound("Class or subject not found.")
            if subject.school_level is not school_class.school_level:
                raise ValidationFailed(
                    "The subject and class are for different levels.",
                    details={"field": "subject_id"},
                )
            if linked:
                await self._ensure_teacher(uow, teacher_id)
                await uow.academics.link_subject(class_id, subject_id, teacher_id)
                school_class.subject_ids.add(subject_id)
            else:
                await uow.academics.unlink_subject(class_id, subject_id)
                school_class.subject_ids.discard(subject_id)
            await uow.commit()
        return ClassView.of(school_class)


# ---- enrolment ---------------------------------------------------------------------------


class Enrolments:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def enrol(self, actor: Principal, *, student_id: UUID, class_id: UUID) -> EnrolmentView:
        """Enrol into the class for the current session.

        Moving class withdraws the old enrolment first.
        """
        actor.require_realm(Realm.SCHOOL)
        actor.require("students:update")
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            term = await uow.academics.current_term()
            if term is None:
                raise ValidationFailed(
                    "Set the current term before enrolling students.", code="no_current_term"
                )
            school_class = await uow.academics.get_class(class_id, for_update=True)
            if school_class is None or not school_class.active:
                raise NotFound("Class not found.")
            student = await uow.users.get(student_id)
            if student is None or SchoolRole.STUDENT.value not in student.roles:
                raise ValidationFailed(
                    "Pick a student from your school.", details={"field": "student_id"}
                )
            existing = await uow.academics.active_enrolment(student_id, term.session_id)
            if existing is not None:
                if existing.class_id == class_id:
                    return EnrolmentView.of(existing)
                existing.status = EnrolmentStatus.WITHDRAWN
                await uow.academics.save_enrolment(existing)
            if not school_class.has_room(
                await uow.academics.count_active_in_class(class_id, term.session_id)
            ):
                raise Conflict("This class is full.", code="class_full")
            enrolment = Enrolment.create(
                tenant_id=tenant_id,
                student_id=student_id,
                class_id=class_id,
                session_id=term.session_id,
                now=self._clock.now(),
            )
            await uow.academics.add_enrolment(enrolment)
            await uow.commit()
        return EnrolmentView.of(enrolment)

    async def roster(self, actor: Principal, class_id: UUID) -> list[EnrolmentView]:
        actor.require_realm(Realm.SCHOOL)
        actor.require("students:read")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            term = await uow.academics.current_term()
            if term is None:
                return []
            enrolments = await uow.academics.roster(class_id, term.session_id)
        return [EnrolmentView.of(e) for e in enrolments]


__all__ = [
    "AcademicCalendar",
    "ClassesAndSubjects",
    "CreateClassCommand",
    "Enrolments",
    "normalise_code",
]
