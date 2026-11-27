"""Academics repository. Every query also filters on tenant_id (defence in depth beside RLS)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.academics.structure import (
    AcademicSession,
    Enrolment,
    EnrolmentStatus,
    SchoolClass,
    SchoolLevelCode,
    Subject,
    Term,
    TermCode,
)
from app.infrastructure.persistence.models.academics import (
    AcademicSessionRow,
    ClassSubjectRow,
    EnrolmentRow,
    SchoolClassRow,
    SubjectRow,
    TermRow,
)
from app.infrastructure.persistence.repositories.scope_state import ScopeState


def _session(r: AcademicSessionRow) -> AcademicSession:
    return AcademicSession(
        id=r.id,
        tenant_id=r.tenant_id,
        name=r.name,
        starts_on=r.starts_on,
        ends_on=r.ends_on,
        is_current=r.is_current,
        created_at=r.created_at,
    )


def _term(r: TermRow) -> Term:
    return Term(
        id=r.id,
        tenant_id=r.tenant_id,
        session_id=r.session_id,
        code=TermCode(r.code),
        name=r.name,
        starts_on=r.starts_on,
        ends_on=r.ends_on,
        is_current=r.is_current,
    )


def _subject(r: SubjectRow) -> Subject:
    return Subject(
        id=r.id,
        tenant_id=r.tenant_id,
        name=r.name,
        code=r.code,
        school_level=SchoolLevelCode(r.school_level),
        description=r.description,
        active=r.active,
    )


def _enrolment(r: EnrolmentRow) -> Enrolment:
    return Enrolment(
        id=r.id,
        tenant_id=r.tenant_id,
        student_id=r.student_id,
        class_id=r.class_id,
        session_id=r.session_id,
        status=EnrolmentStatus(r.status),
        enrolled_at=r.enrolled_at,
    )


class SqlAcademicsRepository:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    @property
    def _tid(self) -> UUID:
        if self._scope.tenant_id is None:
            raise RuntimeError("Academics data needs a tenant scope.")
        return self._scope.tenant_id

    # sessions and terms
    async def add_session(self, session: AcademicSession) -> None:
        self._s.add(
            AcademicSessionRow(
                tenant_id=session.tenant_id,
                id=session.id,
                name=session.name,
                starts_on=session.starts_on,
                ends_on=session.ends_on,
                is_current=session.is_current,
                created_at=session.created_at,
            )
        )
        await self._s.flush()

    async def get_session(self, session_id: UUID) -> AcademicSession | None:
        row = await self._s.get(AcademicSessionRow, (self._tid, session_id))
        return _session(row) if row else None

    async def list_sessions(self) -> list[AcademicSession]:
        rows = await self._s.scalars(
            select(AcademicSessionRow).where(AcademicSessionRow.tenant_id == self._tid)
        )
        return [_session(r) for r in rows]

    async def add_term(self, term: Term) -> None:
        self._s.add(
            TermRow(
                tenant_id=term.tenant_id,
                id=term.id,
                session_id=term.session_id,
                code=term.code.value,
                name=term.name,
                starts_on=term.starts_on,
                ends_on=term.ends_on,
                is_current=term.is_current,
            )
        )
        await self._s.flush()

    async def get_term(self, term_id: UUID) -> Term | None:
        row = await self._s.get(TermRow, (self._tid, term_id))
        return _term(row) if row else None

    async def list_terms(self, session_id: UUID) -> list[Term]:
        rows = await self._s.scalars(
            select(TermRow).where(TermRow.tenant_id == self._tid, TermRow.session_id == session_id)
        )
        return [_term(r) for r in rows]

    async def make_current(self, term: Term) -> None:
        tid = self._tid
        # Clear first, then set: the partial unique indexes allow only one current row per school.
        await self._s.execute(
            update(TermRow)
            .where(TermRow.tenant_id == tid, TermRow.is_current)
            .values(is_current=False)
        )
        await self._s.execute(
            update(AcademicSessionRow)
            .where(AcademicSessionRow.tenant_id == tid, AcademicSessionRow.is_current)
            .values(is_current=False)
        )
        await self._s.execute(
            update(TermRow)
            .where(TermRow.tenant_id == tid, TermRow.id == term.id)
            .values(is_current=True)
        )
        await self._s.execute(
            update(AcademicSessionRow)
            .where(AcademicSessionRow.tenant_id == tid, AcademicSessionRow.id == term.session_id)
            .values(is_current=True)
        )

    async def current_term(self) -> Term | None:
        row = await self._s.scalar(
            select(TermRow).where(TermRow.tenant_id == self._tid, TermRow.is_current)
        )
        return _term(row) if row else None

    # classes
    async def _subject_ids(self, class_ids: list[UUID]) -> dict[UUID, set[UUID]]:
        if not class_ids:
            return {}
        rows = await self._s.execute(
            select(ClassSubjectRow.class_id, ClassSubjectRow.subject_id).where(
                ClassSubjectRow.tenant_id == self._tid, ClassSubjectRow.class_id.in_(class_ids)
            )
        )
        out: dict[UUID, set[UUID]] = {}
        for class_id, subject_id in rows.all():
            out.setdefault(class_id, set()).add(subject_id)
        return out

    @staticmethod
    def _class(r: SchoolClassRow, subject_ids: set[UUID]) -> SchoolClass:
        return SchoolClass(
            id=r.id,
            tenant_id=r.tenant_id,
            name=r.name,
            code=r.code,
            school_level=SchoolLevelCode(r.school_level),
            class_teacher_id=r.class_teacher_id,
            capacity=r.capacity,
            active=r.active,
            subject_ids=subject_ids,
        )

    async def add_class(self, school_class: SchoolClass) -> None:
        self._s.add(
            SchoolClassRow(
                tenant_id=school_class.tenant_id,
                id=school_class.id,
                name=school_class.name,
                code=school_class.code,
                school_level=school_class.school_level.value,
                class_teacher_id=school_class.class_teacher_id,
                capacity=school_class.capacity,
                active=school_class.active,
            )
        )
        await self._s.flush()

    async def get_class(self, class_id: UUID, *, for_update: bool = False) -> SchoolClass | None:
        # for_update serialises enrolments into one class so capacity can't be overrun.
        row = await self._s.get(
            SchoolClassRow, (self._tid, class_id), with_for_update=for_update or None
        )
        if row is None:
            return None
        return self._class(row, (await self._subject_ids([row.id])).get(row.id, set()))

    async def save_class(self, school_class: SchoolClass) -> None:
        await self._s.execute(
            update(SchoolClassRow)
            .where(SchoolClassRow.tenant_id == self._tid, SchoolClassRow.id == school_class.id)
            .values(
                name=school_class.name,
                capacity=school_class.capacity,
                class_teacher_id=school_class.class_teacher_id,
                active=school_class.active,
            )
        )

    async def list_classes(self, *, level: str | None, include_inactive: bool) -> list[SchoolClass]:
        stmt = select(SchoolClassRow).where(SchoolClassRow.tenant_id == self._tid)
        if level:
            stmt = stmt.where(SchoolClassRow.school_level == level)
        if not include_inactive:
            stmt = stmt.where(SchoolClassRow.active)
        rows = list((await self._s.scalars(stmt)).all())
        links = await self._subject_ids([r.id for r in rows])
        return [self._class(r, links.get(r.id, set())) for r in rows]

    async def class_code_exists(self, code: str) -> bool:
        return bool(
            await self._s.scalar(
                select(
                    exists().where(
                        SchoolClassRow.tenant_id == self._tid, SchoolClassRow.code == code
                    )
                )
            )
        )

    # subjects
    async def add_subject(self, subject: Subject) -> None:
        self._s.add(
            SubjectRow(
                tenant_id=subject.tenant_id,
                id=subject.id,
                name=subject.name,
                code=subject.code,
                school_level=subject.school_level.value,
                description=subject.description,
                active=subject.active,
            )
        )
        await self._s.flush()

    async def get_subject(self, subject_id: UUID) -> Subject | None:
        row = await self._s.get(SubjectRow, (self._tid, subject_id))
        return _subject(row) if row else None

    async def list_subjects(self, *, level: str | None) -> list[Subject]:
        stmt = select(SubjectRow).where(SubjectRow.tenant_id == self._tid, SubjectRow.active)
        if level:
            stmt = stmt.where(SubjectRow.school_level == level)
        return [_subject(r) for r in await self._s.scalars(stmt)]

    async def subject_code_exists(self, code: str, level: str) -> bool:
        return bool(
            await self._s.scalar(
                select(
                    exists().where(
                        SubjectRow.tenant_id == self._tid,
                        SubjectRow.code == code,
                        SubjectRow.school_level == level,
                    )
                )
            )
        )

    async def link_subject(self, class_id: UUID, subject_id: UUID, teacher_id: UUID | None) -> None:
        stmt = insert(ClassSubjectRow).values(
            tenant_id=self._tid, class_id=class_id, subject_id=subject_id, teacher_id=teacher_id
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    ClassSubjectRow.tenant_id,
                    ClassSubjectRow.class_id,
                    ClassSubjectRow.subject_id,
                ],
                set_={"teacher_id": stmt.excluded.teacher_id},
            )
        )

    async def unlink_subject(self, class_id: UUID, subject_id: UUID) -> None:
        await self._s.execute(
            delete(ClassSubjectRow).where(
                ClassSubjectRow.tenant_id == self._tid,
                ClassSubjectRow.class_id == class_id,
                ClassSubjectRow.subject_id == subject_id,
            )
        )

    async def teaches_class(self, teacher_id: UUID, class_id: UUID) -> bool:
        homeroom = exists().where(
            SchoolClassRow.tenant_id == self._tid,
            SchoolClassRow.id == class_id,
            SchoolClassRow.class_teacher_id == teacher_id,
        )
        subject = exists().where(
            ClassSubjectRow.tenant_id == self._tid,
            ClassSubjectRow.class_id == class_id,
            ClassSubjectRow.teacher_id == teacher_id,
        )
        return bool(await self._s.scalar(select(homeroom | subject)))

    async def class_of_student(self, student_id: UUID, session_id: UUID) -> UUID | None:
        result: UUID | None = await self._s.scalar(
            select(EnrolmentRow.class_id).where(
                EnrolmentRow.tenant_id == self._tid,
                EnrolmentRow.student_id == student_id,
                EnrolmentRow.session_id == session_id,
                EnrolmentRow.status == EnrolmentStatus.ACTIVE.value,
            )
        )
        return result

    async def classes_of_students(self, student_ids: set[UUID], session_id: UUID) -> set[UUID]:
        if not student_ids:
            return set()
        rows = await self._s.scalars(
            select(EnrolmentRow.class_id).where(
                EnrolmentRow.tenant_id == self._tid,
                EnrolmentRow.student_id.in_(sorted(student_ids)),
                EnrolmentRow.session_id == session_id,
                EnrolmentRow.status == EnrolmentStatus.ACTIVE.value,
            )
        )
        return set(rows)

    async def classes_taught_by(self, teacher_id: UUID) -> set[UUID]:
        homeroom = select(SchoolClassRow.id).where(
            SchoolClassRow.tenant_id == self._tid, SchoolClassRow.class_teacher_id == teacher_id
        )
        subject = select(ClassSubjectRow.class_id).where(
            ClassSubjectRow.tenant_id == self._tid, ClassSubjectRow.teacher_id == teacher_id
        )
        return set(await self._s.scalars(homeroom.union(subject)))

    # enrolments
    async def add_enrolment(self, enrolment: Enrolment) -> None:
        self._s.add(
            EnrolmentRow(
                tenant_id=enrolment.tenant_id,
                id=enrolment.id,
                student_id=enrolment.student_id,
                class_id=enrolment.class_id,
                session_id=enrolment.session_id,
                status=enrolment.status.value,
                enrolled_at=enrolment.enrolled_at,
            )
        )
        await self._s.flush()

    async def active_enrolment(self, student_id: UUID, session_id: UUID) -> Enrolment | None:
        row = await self._s.scalar(
            select(EnrolmentRow)
            .where(
                EnrolmentRow.tenant_id == self._tid,
                EnrolmentRow.student_id == student_id,
                EnrolmentRow.session_id == session_id,
                EnrolmentRow.status == EnrolmentStatus.ACTIVE.value,
            )
            .with_for_update()
        )
        return _enrolment(row) if row else None

    async def save_enrolment(self, enrolment: Enrolment) -> None:
        await self._s.execute(
            update(EnrolmentRow)
            .where(EnrolmentRow.tenant_id == self._tid, EnrolmentRow.id == enrolment.id)
            .values(status=enrolment.status.value)
        )

    async def count_active_in_class(self, class_id: UUID, session_id: UUID) -> int:
        return int(
            await self._s.scalar(
                select(func.count()).where(
                    EnrolmentRow.tenant_id == self._tid,
                    EnrolmentRow.class_id == class_id,
                    EnrolmentRow.session_id == session_id,
                    EnrolmentRow.status == EnrolmentStatus.ACTIVE.value,
                )
            )
            or 0
        )

    async def roster(self, class_id: UUID, session_id: UUID) -> list[Enrolment]:
        rows = await self._s.scalars(
            select(EnrolmentRow)
            .where(
                EnrolmentRow.tenant_id == self._tid,
                EnrolmentRow.class_id == class_id,
                EnrolmentRow.session_id == session_id,
                EnrolmentRow.status == EnrolmentStatus.ACTIVE.value,
            )
            .order_by(EnrolmentRow.enrolled_at)
        )
        return [_enrolment(r) for r in rows]
