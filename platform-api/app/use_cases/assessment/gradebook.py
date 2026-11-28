"""Score sheets: teachers record marks, school leaders approve them.

- The subject teacher of a class (``results:create``) records each student's marks for each part
  of the scheme, marks students who don't take the subject, and submits a complete sheet.
- A leader with ``results:approve`` approves it (not their own), sends it back with a reason, or
  reopens an approved sheet with a reason. Leaders can also correct marks on a draft.
- The class teacher and leaders with ``results:view_all`` can read every sheet of a class.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.domain.academics.sections import SectionFeature
from app.domain.academics.structure import SchoolClass, Term
from app.domain.assessment.gradebook import Mark, ScoreSheet, SheetStatus, check_score
from app.domain.assessment.scheme import Component
from app.domain.communication.inbox import Notification, NotificationKind
from app.domain.identity.roles import Realm, SchoolRole
from app.domain.shared.errors import (
    InvalidStateTransition,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from app.use_cases.academics.sections import require_feature, scope_for, scope_of
from app.use_cases.assessment.setup import ComponentView, scheme_for
from app.use_cases.shared import audit
from app.use_cases.shared.ports import Clock, SchoolTime, Scope, UnitOfWork, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal

ENTER = "results:create"
APPROVE = "results:approve"
VIEW_ALL = "results:view_all"
MAX_ENTRIES = 3_000
_LEADER_ROLES = frozenset(
    {
        SchoolRole.HEAD_TEACHER.value,
        SchoolRole.ASSISTANT_HEAD_TEACHER.value,
        SchoolRole.PRINCIPAL.value,
        SchoolRole.VICE_PRINCIPAL.value,
    }
)


@dataclass(frozen=True, slots=True)
class StudentMarks:
    student_id: UUID
    student_name: str
    exempt: bool
    marks: dict[UUID, Decimal]  # by part
    total: Decimal | None  # None until every part has a mark
    missing: int


@dataclass(frozen=True, slots=True)
class SheetView:
    id: UUID | None
    term_id: UUID
    class_id: UUID
    class_name: str
    subject_id: UUID
    subject_name: str
    teacher_id: UUID | None
    teacher_name: str | None
    status: str
    note: str | None
    submitted_by: UUID | None
    submitted_at: datetime | None
    approved_by: UUID | None
    approved_at: datetime | None
    components: list[ComponentView]
    students: list[StudentMarks]
    missing: int
    can_edit: bool
    can_approve: bool


@dataclass(frozen=True, slots=True)
class SheetSummary:
    id: UUID | None
    class_id: UUID
    class_name: str
    subject_id: UUID
    subject_name: str
    teacher_id: UUID | None
    teacher_name: str | None
    status: str
    missing: int
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class Entry:
    student_id: UUID
    component_id: UUID
    score: Decimal | None  # None clears the mark


class Gradebook:
    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, clock: Clock, school_time: SchoolTime
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._school_time = school_time

    # ---- reading ----------------------------------------------------------------------------

    async def sheet(
        self, actor: Principal, *, term_id: UUID, class_id: UUID, subject_id: UUID
    ) -> SheetView:
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            term, cls = await _term_and_class(uow, actor, term_id, class_id, subject_id)
            teacher = await uow.academics.subject_teacher(cls.id, subject_id)
            _may_read(actor, cls, teacher)
            sheet = await uow.assessment.find_sheet(term.id, cls.id, subject_id)
            view = await self._view(uow, actor, term, cls, subject_id, teacher, sheet)
            await uow.commit()  # audit: not needed. Reading; may save the default scheme.
        return view

    async def class_overview(
        self, actor: Principal, *, term_id: UUID, class_id: UUID
    ) -> list[SheetSummary]:
        """Every subject the class takes, with its teacher and where its sheet stands."""
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            term = await _term(uow, term_id)
            cls = await _class(uow, class_id)
            await in_section(uow, actor, cls)
            if not (
                cls.class_teacher_id == actor.user_id
                or actor.permissions.allows(VIEW_ALL)
                or actor.permissions.allows(APPROVE)
            ):
                raise PermissionDenied("For the class teacher and school leaders.")
            teachers = await uow.academics.class_subject_teachers(cls.id)
            return await self._summaries(
                uow, term, [(cls, subject_id, teachers.get(subject_id)) for subject_id in teachers]
            )

    async def my_sheets(self, actor: Principal, *, term_id: UUID) -> list[SheetSummary]:
        """The class and subject pairs the teacher teaches, with each sheet's status."""
        actor.require_realm(Realm.SCHOOL)
        actor.require(ENTER)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            term = await _term(uow, term_id)
            pairs: list[tuple[SchoolClass, UUID, UUID | None]] = []
            for class_id, subject_id in await uow.academics.subjects_taught(actor.user_id):
                cls = await uow.academics.get_class(class_id)
                if cls is not None and cls.active:
                    pairs.append((cls, subject_id, actor.user_id))
            return await self._summaries(uow, term, pairs)

    async def awaiting_approval(self, actor: Principal, *, term_id: UUID) -> list[SheetSummary]:
        actor.require_realm(Realm.SCHOOL)
        actor.require(APPROVE)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            term = await _term(uow, term_id)
            scope = await scope_of(uow, actor)
            pairs = []
            for sheet in await uow.assessment.sheets(term_id=term.id, status=SheetStatus.SUBMITTED):
                cls = await uow.academics.get_class(sheet.class_id)
                if cls is not None and scope.covers(cls.school_level):
                    teacher = await uow.academics.subject_teacher(cls.id, sheet.subject_id)
                    pairs.append((cls, sheet.subject_id, teacher))
            return await self._summaries(uow, term, pairs)

    # ---- entering marks ---------------------------------------------------------------------

    async def save_marks(
        self,
        actor: Principal,
        *,
        term_id: UUID,
        class_id: UUID,
        subject_id: UUID,
        entries: list[Entry],
    ) -> SheetView:
        actor.require_realm(Realm.SCHOOL)
        if not entries:
            raise ValidationFailed("Nothing to save.", details={"field": "entries"})
        if len(entries) > MAX_ENTRIES:
            raise ValidationFailed(f"Save at most {MAX_ENTRIES} marks at a time.")
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        today = (await self._school_time.now_local(tenant_id)).date()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            term, cls = await _term_and_class(uow, actor, term_id, class_id, subject_id)
            if term.starts_on > today:
                raise InvalidStateTransition("That term hasn't started.", code="term_not_started")
            teacher = await uow.academics.subject_teacher(cls.id, subject_id)
            _may_edit(actor, teacher)
            sheet = await self._open_sheet(uow, term, cls, subject_id, now)
            sheet.touch(now)
            components = {c.id: c for c in await scheme_for(uow, tenant_id, cls.school_level.value)}
            roster = {e.student_id for e in await uow.academics.roster(cls.id, term.session_id)}
            exempt = await uow.assessment.exemptions(sheet.id)
            current = {
                (m.student_id, m.component_id): m.score
                for m in await uow.assessment.marks(sheet.id)
            }
            to_save: dict[tuple[UUID, UUID], Mark] = {}
            to_clear: set[tuple[UUID, UUID]] = set()
            for entry in entries:
                part = components.get(entry.component_id)
                if part is None:
                    raise ValidationFailed(
                        "That part isn't in this term's scheme.",
                        details={"component_id": str(entry.component_id)},
                    )
                if entry.student_id not in roster:
                    raise ValidationFailed(
                        "That student isn't in this class.",
                        details={"student_id": str(entry.student_id)},
                        code="not_in_class",
                    )
                if entry.student_id in exempt:
                    raise ValidationFailed(
                        "That student doesn't take this subject.",
                        details={"student_id": str(entry.student_id)},
                        code="student_exempt",
                    )
                key = (entry.student_id, entry.component_id)
                if entry.score is None:
                    to_save.pop(key, None)
                    if key in current:
                        to_clear.add(key)
                    continue
                to_clear.discard(key)
                to_save[key] = Mark(
                    student_id=entry.student_id,
                    component_id=entry.component_id,
                    score=_checked(entry.score, part),
                )
            changed = [m for k, m in to_save.items() if current.get(k) != m.score]
            await uow.assessment.save_marks(sheet.id, changed, by=actor.user_id, now=now)
            await uow.assessment.clear_marks(sheet.id, sorted(to_clear))
            await uow.assessment.save_sheet(sheet)
            if changed or to_clear:
                uow.audit(
                    audit.by(
                        actor,
                        "assessment.marks_saved",
                        target_type="score_sheet",
                        target_id=sheet.id,
                        changes=[
                            {
                                "student_id": str(m.student_id),
                                "part": components[m.component_id].code,
                                "from": _str(current.get((m.student_id, m.component_id))),
                                "to": str(m.score),
                            }
                            for m in changed[:200]
                        ]
                        + [
                            {
                                "student_id": str(s),
                                "part": components[c].code,
                                "from": _str(current.get((s, c))),
                                "to": None,
                            }
                            for s, c in sorted(to_clear)[:200]
                        ],
                        changed=len(changed) + len(to_clear),
                    )
                )
            view = await self._view(uow, actor, term, cls, subject_id, teacher, sheet)
            await uow.commit()
        return view

    async def set_exempt(
        self,
        actor: Principal,
        *,
        term_id: UUID,
        class_id: UUID,
        subject_id: UUID,
        student_id: UUID,
        exempt: bool,
    ) -> SheetView:
        """Marks a student as not taking this subject (their marks are removed), or undoes it."""
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            term, cls = await _term_and_class(uow, actor, term_id, class_id, subject_id)
            teacher = await uow.academics.subject_teacher(cls.id, subject_id)
            _may_edit(actor, teacher)
            if student_id not in {
                e.student_id for e in await uow.academics.roster(cls.id, term.session_id)
            }:
                raise ValidationFailed("That student isn't in this class.", code="not_in_class")
            sheet = await self._open_sheet(uow, term, cls, subject_id, now)
            sheet.touch(now)
            if exempt:
                keys = [
                    (m.student_id, m.component_id)
                    for m in await uow.assessment.marks(sheet.id)
                    if m.student_id == student_id
                ]
                await uow.assessment.clear_marks(sheet.id, keys)
            await uow.assessment.set_exempt(sheet.id, student_id, exempt=exempt)
            await uow.assessment.save_sheet(sheet)
            uow.audit(
                audit.by(
                    actor,
                    "assessment.student_exempted" if exempt else "assessment.exemption_removed",
                    target_type="score_sheet",
                    target_id=sheet.id,
                    student_id=str(student_id),
                )
            )
            view = await self._view(uow, actor, term, cls, subject_id, teacher, sheet)
            await uow.commit()
        return view

    # ---- the approval flow ------------------------------------------------------------------

    async def submit(self, actor: Principal, sheet_id: UUID) -> SheetView:
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            sheet, term, cls, teacher = await self._load(uow, actor, sheet_id)
            _may_edit(actor, teacher)
            view = await self._view(uow, actor, term, cls, sheet.subject_id, teacher, sheet)
            sheet.submit(by=actor.user_id, missing=view.missing, now=now)
            await uow.assessment.save_sheet(sheet)
            approvers = [
                person
                for person in await uow.users.school_users_with_access(
                    tenant_id, roles=_LEADER_ROLES, capability=APPROVE
                )
                # Only the leaders of this class's section are told.
                if (await scope_for(uow, person.id, person.roles)).covers(cls.school_level)
            ]
            await uow.communication.notify(
                [
                    Notification.new(
                        tenant_id=tenant_id,
                        user_id=person.id,
                        kind=NotificationKind.GENERAL,
                        title=f"{view.subject_name} marks for {cls.name} are ready",
                        body="Submitted for approval.",
                        link_type="score_sheet",
                        link_id=sheet.id,
                        now=now,
                    )
                    for person in approvers
                    if person.id != actor.user_id
                ]
            )
            uow.audit(
                audit.by(
                    actor,
                    "assessment.sheet_submitted",
                    target_type="score_sheet",
                    target_id=sheet.id,
                )
            )
            view = await self._view(uow, actor, term, cls, sheet.subject_id, teacher, sheet)
            await uow.commit()
        return view

    async def recall(self, actor: Principal, sheet_id: UUID) -> SheetView:
        actor.require_realm(Realm.SCHOOL)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            sheet, term, cls, teacher = await self._load(uow, actor, sheet_id)
            sheet.recall(by=actor.user_id, now=now)
            await uow.assessment.save_sheet(sheet)
            uow.audit(
                audit.by(
                    actor,
                    "assessment.sheet_recalled",
                    target_type="score_sheet",
                    target_id=sheet.id,
                )
            )
            view = await self._view(uow, actor, term, cls, sheet.subject_id, teacher, sheet)
            await uow.commit()
        return view

    async def approve(self, actor: Principal, sheet_id: UUID) -> SheetView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(APPROVE)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            sheet, term, cls, teacher = await self._load(uow, actor, sheet_id)
            (await scope_of(uow, actor)).require(cls.school_level)
            submitter = sheet.submitted_by
            sheet.approve(by=actor.user_id, now=now)
            await uow.assessment.save_sheet(sheet)
            await self._tell(uow, sheet, cls, submitter, "approved", None, now)
            uow.audit(
                audit.by(
                    actor,
                    "assessment.sheet_approved",
                    target_type="score_sheet",
                    target_id=sheet.id,
                )
            )
            view = await self._view(uow, actor, term, cls, sheet.subject_id, teacher, sheet)
            await uow.commit()
        return view

    async def send_back(self, actor: Principal, sheet_id: UUID, *, note: str | None) -> SheetView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(APPROVE)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            sheet, term, cls, teacher = await self._load(uow, actor, sheet_id)
            (await scope_of(uow, actor)).require(cls.school_level)
            submitter = sheet.submitted_by
            sheet.send_back(note=note, now=now)
            await uow.assessment.save_sheet(sheet)
            await self._tell(uow, sheet, cls, submitter, "sent back", sheet.note, now)
            uow.audit(
                audit.by(
                    actor,
                    "assessment.sheet_sent_back",
                    target_type="score_sheet",
                    target_id=sheet.id,
                    note=sheet.note,
                )
            )
            view = await self._view(uow, actor, term, cls, sheet.subject_id, teacher, sheet)
            await uow.commit()
        return view

    async def reopen(self, actor: Principal, sheet_id: UUID, *, note: str | None) -> SheetView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(APPROVE)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            sheet, term, cls, teacher = await self._load(uow, actor, sheet_id)
            (await scope_of(uow, actor)).require(cls.school_level)
            submitter = sheet.submitted_by
            release = await uow.assessment.get_release(term.id, cls.id)
            if release is not None and release.visible:
                raise InvalidStateTransition(
                    "Results for this class are out. Withdraw them before changing marks.",
                    code="results_released",
                )
            sheet.reopen(note=note, now=now)
            await uow.assessment.save_sheet(sheet)
            await self._tell(uow, sheet, cls, teacher or submitter, "reopened", sheet.note, now)
            uow.audit(
                audit.by(
                    actor,
                    "assessment.sheet_reopened",
                    target_type="score_sheet",
                    target_id=sheet.id,
                    note=sheet.note,
                )
            )
            view = await self._view(uow, actor, term, cls, sheet.subject_id, teacher, sheet)
            await uow.commit()
        return view

    # ---- helpers ----------------------------------------------------------------------------

    async def _open_sheet(
        self, uow: UnitOfWork, term: Term, cls: SchoolClass, subject_id: UUID, now: datetime
    ) -> ScoreSheet:
        sheet = await uow.assessment.find_sheet(term.id, cls.id, subject_id, for_update=True)
        if sheet is not None:
            return sheet
        sheet = ScoreSheet.open(
            tenant_id=cls.tenant_id,
            term_id=term.id,
            class_id=cls.id,
            subject_id=subject_id,
            now=now,
        )
        await uow.assessment.add_sheet(sheet)
        return sheet

    async def _load(
        self, uow: UnitOfWork, actor: Principal, sheet_id: UUID
    ) -> tuple[ScoreSheet, Term, SchoolClass, UUID | None]:
        sheet = await uow.assessment.get_sheet(sheet_id, for_update=True)
        if sheet is None:
            raise NotFound("Score sheet not found.")
        term = await _term(uow, sheet.term_id)
        cls = await _class(uow, sheet.class_id)
        await in_section(uow, actor, cls)
        await require_feature(uow, cls.school_level, SectionFeature.GRADEBOOK)
        teacher = await uow.academics.subject_teacher(cls.id, sheet.subject_id)
        return sheet, term, cls, teacher

    async def _tell(
        self,
        uow: UnitOfWork,
        sheet: ScoreSheet,
        cls: SchoolClass,
        user_id: UUID | None,
        what: str,
        note: str | None,
        now: datetime,
    ) -> None:
        if user_id is None:
            return
        subject = await uow.academics.get_subject(sheet.subject_id)
        await uow.communication.notify(
            [
                Notification.new(
                    tenant_id=sheet.tenant_id,
                    user_id=user_id,
                    kind=NotificationKind.GENERAL,
                    title=f"{subject.name if subject else 'Subject'} marks for {cls.name} {what}",
                    body=note or "",
                    link_type="score_sheet",
                    link_id=sheet.id,
                    now=now,
                )
            ]
        )

    async def _view(
        self,
        uow: UnitOfWork,
        actor: Principal,
        term: Term,
        cls: SchoolClass,
        subject_id: UUID,
        teacher: UUID | None,
        sheet: ScoreSheet | None,
    ) -> SheetView:
        components = await scheme_for(uow, cls.tenant_id, cls.school_level.value)
        roster = await uow.academics.roster(cls.id, term.session_id)
        marks = await uow.assessment.marks(sheet.id) if sheet else []
        exempt = await uow.assessment.exemptions(sheet.id) if sheet else set()
        ids = {e.student_id for e in roster} | ({teacher} if teacher else set())
        names = await uow.users.school_names(ids)
        subject = await uow.academics.get_subject(subject_id)
        by_student: dict[UUID, dict[UUID, Decimal]] = {}
        for m in marks:
            by_student.setdefault(m.student_id, {})[m.component_id] = m.score
        students = []
        for e in roster:
            got = by_student.get(e.student_id, {})
            excused = e.student_id in exempt
            missing = 0 if excused else sum(1 for c in components if c.id not in got)
            students.append(
                StudentMarks(
                    student_id=e.student_id,
                    student_name=names.get(e.student_id, ""),
                    exempt=excused,
                    marks={c.id: got[c.id] for c in components if c.id in got},
                    total=None
                    if excused or missing
                    else sum((got[c.id] for c in components), Decimal(0)),
                    missing=missing,
                )
            )
        students.sort(key=lambda s: s.student_name)
        status = sheet.status if sheet else SheetStatus.DRAFT
        editable = status is SheetStatus.DRAFT and _can_edit(actor, teacher)
        return SheetView(
            id=sheet.id if sheet else None,
            term_id=term.id,
            class_id=cls.id,
            class_name=cls.name,
            subject_id=subject_id,
            subject_name=subject.name if subject else "",
            teacher_id=teacher,
            teacher_name=names.get(teacher) if teacher else None,
            status=status.value,
            note=sheet.note if sheet else None,
            submitted_by=sheet.submitted_by if sheet else None,
            submitted_at=sheet.submitted_at if sheet else None,
            approved_by=sheet.approved_by if sheet else None,
            approved_at=sheet.approved_at if sheet else None,
            components=[
                ComponentView(
                    id=c.id, code=c.code, name=c.name, kind=c.kind.value, max_score=c.max_score
                )
                for c in components
            ],
            students=students,
            missing=sum(s.missing for s in students),
            can_edit=editable,
            can_approve=status is SheetStatus.SUBMITTED
            and actor.permissions.allows(APPROVE)
            and sheet is not None
            and sheet.submitted_by != actor.user_id,
        )

    async def _summaries(
        self, uow: UnitOfWork, term: Term, pairs: list[tuple[SchoolClass, UUID, UUID | None]]
    ) -> list[SheetSummary]:
        out = []
        components_by_level: dict[str, list[Component]] = {}
        names = await uow.users.school_names({t for _, _, t in pairs if t})
        for cls, subject_id, teacher in pairs:
            level = cls.school_level.value
            if level not in components_by_level:
                components_by_level[level] = await uow.assessment.components(level)
            parts = len(components_by_level[level])
            sheet = await uow.assessment.find_sheet(term.id, cls.id, subject_id)
            roster = await uow.academics.roster(cls.id, term.session_id)
            if sheet is None:
                missing = parts * len(roster)
            else:
                exempt = await uow.assessment.exemptions(sheet.id)
                have: dict[UUID, int] = {}
                for m in await uow.assessment.marks(sheet.id):
                    have[m.student_id] = have.get(m.student_id, 0) + 1
                missing = sum(
                    max(parts - have.get(e.student_id, 0), 0)
                    for e in roster
                    if e.student_id not in exempt
                )
            subject = await uow.academics.get_subject(subject_id)
            out.append(
                SheetSummary(
                    id=sheet.id if sheet else None,
                    class_id=cls.id,
                    class_name=cls.name,
                    subject_id=subject_id,
                    subject_name=subject.name if subject else "",
                    teacher_id=teacher,
                    teacher_name=names.get(teacher) if teacher else None,
                    status=(sheet.status if sheet else SheetStatus.DRAFT).value,
                    missing=missing,
                    updated_at=sheet.updated_at if sheet else None,
                )
            )
        return sorted(out, key=lambda s: (s.class_name, s.subject_name))


def _checked(score: Decimal, part: Component) -> Decimal:
    try:
        return check_score(score, part.max_score)
    except ValidationFailed as exc:
        raise ValidationFailed(
            exc.message, details={**exc.details, "component_id": str(part.id)}, code=exc.code
        ) from exc


def _str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _can_edit(actor: Principal, teacher: UUID | None) -> bool:
    teaches = teacher == actor.user_id and actor.permissions.allows(ENTER)
    return teaches or actor.permissions.allows(APPROVE)


def _may_edit(actor: Principal, teacher: UUID | None) -> None:
    if not _can_edit(actor, teacher):
        raise PermissionDenied("Only this subject's teacher or a school leader can enter marks.")


def _may_read(actor: Principal, cls: SchoolClass, teacher: UUID | None) -> None:
    if (
        _can_edit(actor, teacher)
        or cls.class_teacher_id == actor.user_id
        or actor.permissions.allows(VIEW_ALL)
    ):
        return
    raise PermissionDenied("For this subject's teacher, the class teacher and school leaders.")


async def _term(uow: UnitOfWork, term_id: UUID) -> Term:
    term = await uow.academics.get_term(term_id)
    if term is None:
        raise NotFound("Term not found.")
    return term


async def _class(uow: UnitOfWork, class_id: UUID) -> SchoolClass:
    cls = await uow.academics.get_class(class_id)
    if cls is None:
        raise NotFound("Class not found.")
    return cls


async def in_section(uow: UnitOfWork, actor: Principal, cls: SchoolClass) -> None:
    """Leaders act only on their own section's classes; teachers on the classes they teach."""
    if (await scope_of(uow, actor)).covers(cls.school_level):
        return
    if cls.class_teacher_id == actor.user_id or await uow.academics.teaches_class(
        actor.user_id, cls.id
    ):
        return
    raise PermissionDenied("This class is in another section of the school.", code="other_section")


async def _term_and_class(
    uow: UnitOfWork, actor: Principal, term_id: UUID, class_id: UUID, subject_id: UUID
) -> tuple[Term, SchoolClass]:
    term = await _term(uow, term_id)
    cls = await _class(uow, class_id)
    await in_section(uow, actor, cls)
    await require_feature(uow, cls.school_level, SectionFeature.GRADEBOOK)
    if subject_id not in cls.subject_ids:
        raise NotFound("This class doesn't take that subject.")
    return term, cls
