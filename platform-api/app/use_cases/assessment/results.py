"""Results and report cards: from approved marks to what families see.

- Leaders and the class teacher preview a class's broadsheet at any time (any sheet status).
- The class teacher writes each student's comment; a leader with ``results:approve`` writes the
  head's comment.
- A leader with ``results:approve`` releases a class's results once every subject's sheet is
  approved. Release freezes each student's report card as it stands (later changes to the
  scale or scheme don't alter it), tells the guardians and students, and emails the guardians.
  A release can be withdrawn with a reason (sheets can then be reopened) and released again.
- Students and guardians see released report cards only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar
from uuid import UUID

from app.domain.academics.structure import SchoolClass, Term
from app.domain.assessment.gradebook import SheetStatus
from app.domain.assessment.repositories import StoredReportCard
from app.domain.assessment.results import (
    ClassResult,
    ResultRelease,
    StudentResult,
    SubjectMarks,
    comment_text,
    compute,
)
from app.domain.communication.inbox import Notification, NotificationKind
from app.domain.identity.roles import Realm, SchoolRole
from app.domain.shared.errors import (
    InvalidStateTransition,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from app.domain.shared.events import DomainEvent
from app.use_cases.assessment.setup import scale_for, scheme_for
from app.use_cases.identity.guardians import is_guardian_or_self
from app.use_cases.shared import audit
from app.use_cases.shared.ports import Clock, Scope, UnitOfWork, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal

APPROVE = "results:approve"
VIEW_ALL = "results:view_all"
READ = "results:read"


@dataclass(frozen=True, kw_only=True)
class ResultsReleased(DomainEvent):
    """Emails each guardian that their child's report card is ready."""

    name: ClassVar[str] = "assessment.results_released"
    term_id: UUID
    class_id: UUID
    student_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SubjectStatus:
    subject_id: UUID
    subject_name: str
    status: str  # no_marks, draft, submitted, approved


@dataclass(frozen=True, slots=True)
class Broadsheet:
    term_id: UUID
    class_id: UUID
    class_name: str
    release_status: str | None  # None, released or withdrawn
    release_version: int | None
    subjects: list[SubjectStatus]
    ready_to_release: bool
    class_average: Decimal | None
    cards: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ReportSummary:
    term_id: UUID
    term_name: str
    session_name: str
    class_name: str
    average: str | None
    position: int | None
    out_of: int
    released_at: datetime


class Results:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def broadsheet(self, actor: Principal, *, term_id: UUID, class_id: UUID) -> Broadsheet:
        """Every student's would-be report card from the marks as they stand now."""
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            term, cls = await _term(uow, term_id), await _class(uow, class_id)
            _may_preview(actor, cls)
            statuses, result, cards = await self._build(uow, term, cls, approved_only=False)
            release = await uow.assessment.get_release(term.id, cls.id)
            await uow.commit()  # audit: not needed. Reading; may save the default scheme.
        return Broadsheet(
            term_id=term.id,
            class_id=cls.id,
            class_name=cls.name,
            release_status=release.status.value if release else None,
            release_version=release.version if release else None,
            subjects=statuses,
            ready_to_release=bool(statuses) and all(s.status == "approved" for s in statuses),
            class_average=result.class_average,
            cards=cards,
        )

    async def report_card(
        self, actor: Principal, *, term_id: UUID, student_id: UUID
    ) -> dict[str, Any]:
        """Families see the released card; staff who may preview see the live one before release."""
        actor.require_realm(Realm.SCHOOL)
        actor.require(READ)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            term = await _term(uow, term_id)
            await _student(uow, tenant_id, student_id)
            stored = await uow.assessment.report_card(term.id, student_id)
            if await is_guardian_or_self(uow, actor, student_id):
                if stored is None or not stored.visible:
                    raise NotFound("This report card isn't out yet.", code="not_released")
                await uow.rollback()
                return _with_meta(stored)
            class_id = (
                stored.class_id
                if stored
                else await uow.academics.class_of_student(student_id, term.session_id)
            )
            if class_id is None:
                raise NotFound("This student wasn't in a class that term.")
            cls = await _class(uow, class_id)
            _may_preview(actor, cls)
            if stored is not None and stored.visible:
                await uow.rollback()
                return _with_meta(stored)
            _, _, cards = await self._build(uow, term, cls, approved_only=False)
            await uow.commit()  # audit: not needed. Reading; may save the default scheme.
        for card in cards:
            if card["student"]["id"] == str(student_id):
                return {**card, "released": False}
        raise NotFound("No marks for this student yet.")

    async def report_cards_of(self, actor: Principal, student_id: UUID) -> list[ReportSummary]:
        """Released report cards for a student, newest term first."""
        actor.require_realm(Realm.SCHOOL)
        actor.require(READ)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            await _student(uow, tenant_id, student_id)
            family = await is_guardian_or_self(uow, actor, student_id)
            if not family and not actor.permissions.allows(VIEW_ALL):
                raise PermissionDenied("For the student, their guardians and school leaders.")
            cards = await uow.assessment.report_cards_of(student_id)
        return [
            ReportSummary(
                term_id=c.term_id,
                term_name=c.data["term"]["name"],
                session_name=c.data["term"]["session_name"],
                class_name=c.data["class"]["name"],
                average=c.data["average"],
                position=c.data["position"],
                out_of=c.data["out_of"],
                released_at=c.released_at,
            )
            for c in cards
        ]

    async def set_comment(
        self,
        actor: Principal,
        *,
        term_id: UUID,
        student_id: UUID,
        teacher_comment: str | None = None,
        head_comment: str | None = None,
        set_teacher: bool = False,
        set_head: bool = False,
    ) -> None:
        actor.require_realm(Realm.SCHOOL)
        if not set_teacher and not set_head:
            raise ValidationFailed("Nothing to save.")
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            term = await _term(uow, term_id)
            await _student(uow, tenant_id, student_id)
            class_id = await uow.academics.class_of_student(student_id, term.session_id)
            if class_id is None:
                raise NotFound("This student isn't in a class this session.")
            cls = await _class(uow, class_id)
            if set_teacher and cls.class_teacher_id != actor.user_id:
                raise PermissionDenied("Only the class teacher writes this comment.")
            if set_head:
                actor.require(APPROVE)
            release = await uow.assessment.get_release(term.id, cls.id)
            if release is not None and release.visible:
                raise InvalidStateTransition(
                    "These results are released. Withdraw them to change comments.",
                    code="results_released",
                )
            await uow.assessment.save_comment(
                term.id,
                student_id,
                teacher=(comment_text(teacher_comment), actor.user_id) if set_teacher else None,
                head=(comment_text(head_comment), actor.user_id) if set_head else None,
                now=now,
            )
            uow.audit(
                audit.by(
                    actor,
                    "assessment.comment_saved",
                    target_type="school_user",
                    target_id=student_id,
                    term_id=str(term.id),
                    which="teacher" if set_teacher else "head",
                )
            )
            await uow.commit()

    async def release(self, actor: Principal, *, term_id: UUID, class_id: UUID) -> Broadsheet:
        actor.require_realm(Realm.SCHOOL)
        actor.require(APPROVE)
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            term, cls = await _term(uow, term_id), await _class(uow, class_id)
            release = await uow.assessment.get_release(term.id, cls.id, for_update=True)
            if release is not None and release.visible:
                raise InvalidStateTransition(
                    "These results are already released.", code="already_released"
                )
            # Locks the class's sheets so no sheet is reopened while this release is written.
            await uow.assessment.sheets(term_id=term.id, class_id=cls.id, for_update=True)
            statuses, _result, cards = await self._build(uow, term, cls, approved_only=True)
            waiting = [s.subject_name for s in statuses if s.status != SheetStatus.APPROVED.value]
            if not statuses or waiting:
                raise InvalidStateTransition(
                    "Every subject's marks must be approved first"
                    + (f": {', '.join(waiting)}." if waiting else "."),
                    details={"subjects": waiting},
                    code="not_ready",
                )
            cards = [c for c in cards if c["subjects"]]
            if not cards:
                raise InvalidStateTransition("No student has results.", code="not_ready")
            if release is None:
                release = ResultRelease.first(
                    tenant_id=tenant_id, term_id=term.id, class_id=cls.id, by=actor.user_id, now=now
                )
                await uow.assessment.add_release(release)
            else:
                release.release_again(by=actor.user_id, now=now)
                await uow.assessment.save_release(release)
            await uow.assessment.save_report_cards(
                [
                    StoredReportCard(
                        term_id=term.id,
                        student_id=UUID(c["student"]["id"]),
                        class_id=cls.id,
                        version=release.version,
                        data=c,
                        released_at=now,
                        visible=True,
                    )
                    for c in cards
                ]
            )
            students = [UUID(c["student"]["id"]) for c in cards]
            notices = []
            first_names = {
                UUID(c["student"]["id"]): (c["student"]["name"].split() or ["your child"])[0]
                for c in cards
            }
            for student_id in students:
                first = first_names[student_id]
                readers = [student_id] + [
                    g.parent_id for g in await uow.guardians.guardians_of(student_id)
                ]
                notices += [
                    Notification.new(
                        tenant_id=tenant_id,
                        user_id=reader,
                        kind=NotificationKind.GENERAL,
                        title=f"{term.name} report card for {first} is ready",
                        body=f"{cls.name}.",
                        link_type="report_card",
                        link_id=term.id,
                        now=now,
                    )
                    for reader in readers
                ]
            await uow.communication.notify(notices)
            uow.publish(
                ResultsReleased(
                    tenant_id=tenant_id,
                    term_id=term.id,
                    class_id=cls.id,
                    student_ids=tuple(str(s) for s in students),
                )
            )
            uow.audit(
                audit.by(
                    actor,
                    "assessment.results_released",
                    target_type="school_class",
                    target_id=cls.id,
                    term_id=str(term.id),
                    version=release.version,
                    students=len(students),
                )
            )
            await uow.commit()
        return await self.broadsheet(actor, term_id=term_id, class_id=class_id)

    async def withdraw(
        self, actor: Principal, *, term_id: UUID, class_id: UUID, reason: str | None
    ) -> Broadsheet:
        actor.require_realm(Realm.SCHOOL)
        actor.require(APPROVE)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            release = await uow.assessment.get_release(term_id, class_id, for_update=True)
            if release is None:
                raise NotFound("These results haven't been released.")
            release.withdraw(by=actor.user_id, reason=reason, now=self._clock.now())
            await uow.assessment.save_release(release)
            uow.audit(
                audit.by(
                    actor,
                    "assessment.results_withdrawn",
                    target_type="school_class",
                    target_id=class_id,
                    term_id=str(term_id),
                    reason=release.withdraw_reason,
                )
            )
            await uow.commit()
        return await self.broadsheet(actor, term_id=term_id, class_id=class_id)

    # ---- building ---------------------------------------------------------------------------

    async def _build(
        self, uow: UnitOfWork, term: Term, cls: SchoolClass, *, approved_only: bool
    ) -> tuple[list[SubjectStatus], ClassResult, list[dict[str, Any]]]:
        level = cls.school_level.value
        components = await scheme_for(uow, cls.tenant_id, level)
        codes = {c.id: c.code for c in components}
        bands = await scale_for(uow, level)
        roster = [e.student_id for e in await uow.academics.roster(cls.id, term.session_id)]
        subject_ids = sorted(cls.subject_ids)
        subjects = {s: await uow.academics.get_subject(s) for s in subject_ids}
        sheets = {
            s.subject_id: s for s in await uow.assessment.sheets(term_id=term.id, class_id=cls.id)
        }
        marks = await uow.assessment.marks_for_sheets([s.id for s in sheets.values()])
        statuses: list[SubjectStatus] = []
        inputs: list[SubjectMarks] = []
        for subject_id in subject_ids:
            subject = subjects[subject_id]
            name = subject.name if subject else ""
            sheet = sheets.get(subject_id)
            statuses.append(
                SubjectStatus(
                    subject_id=subject_id,
                    subject_name=name,
                    status=sheet.status.value if sheet else "no_marks",
                )
            )
            if sheet is None or (approved_only and sheet.status is not SheetStatus.APPROVED):
                continue
            per_student: dict[UUID, dict[str, Decimal]] = {}
            for m in marks.get(sheet.id, []):
                if m.component_id in codes:
                    per_student.setdefault(m.student_id, {})[codes[m.component_id]] = m.score
            complete = {
                who: parts for who, parts in per_student.items() if len(parts) == len(components)
            }
            inputs.append(SubjectMarks(subject_id=subject_id, subject_name=name, parts=complete))
        earlier_terms = [
            t.id
            for t in sorted(
                await uow.academics.list_terms(term.session_id), key=lambda t: t.starts_on
            )
            if t.starts_on < term.starts_on
        ]
        earlier: dict[tuple[UUID, UUID], list[Decimal]] = {}
        order = {t: i for i, t in enumerate(earlier_terms)}
        for card in sorted(
            await uow.assessment.released_cards(earlier_terms, set(roster)),
            key=lambda c: order[c.term_id],
        ):
            for line in card.data["subjects"]:
                earlier.setdefault((UUID(line["subject_id"]), card.student_id), []).append(
                    Decimal(line["total"])
                )
        result = compute(students=roster, subjects=inputs, earlier=earlier, bands=bands)
        names = await uow.users.school_names(
            set(roster) | ({cls.class_teacher_id} if cls.class_teacher_id else set())
        )
        comments = await uow.assessment.comments(term.id, set(roster))
        session = await uow.academics.get_session(term.session_id)
        terms = sorted(await uow.academics.list_terms(term.session_id), key=lambda t: t.starts_on)
        later = [t for t in terms if t.starts_on > term.starts_on]
        cards = []
        for student_id in roster:
            attendance = await uow.attendance.student_summary(
                student_id, term.starts_on, min(term.ends_on, self._clock.now().date())
            )
            note = comments.get(student_id)
            cards.append(
                _card(
                    result.students[student_id],
                    name=names.get(student_id, ""),
                    cls=cls,
                    class_teacher=names.get(cls.class_teacher_id) if cls.class_teacher_id else None,
                    term=term,
                    session_name=session.name if session else "",
                    components=[
                        {"code": c.code, "name": c.name, "max_score": c.max_score}
                        for c in components
                    ],
                    scale=[
                        {
                            "grade": b.grade,
                            "remark": b.remark,
                            "min_score": b.min_score,
                            "max_score": b.max_score,
                        }
                        for b in bands
                    ],
                    attendance={
                        "present": attendance.present,
                        "late": attendance.late,
                        "absent": attendance.absent,
                        "excused": attendance.excused,
                        "days": attendance.total,
                    },
                    teacher_comment=note.teacher if note else None,
                    head_comment=note.head if note else None,
                    next_term_starts_on=later[0].starts_on if later else None,
                    class_average=result.class_average,
                )
            )
        cards.sort(key=lambda c: (c["position"] is None, c["position"] or 0, c["student"]["name"]))
        return statuses, result, cards


def _num(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _card(
    r: StudentResult,
    *,
    name: str,
    cls: SchoolClass,
    class_teacher: str | None,
    term: Term,
    session_name: str,
    components: list[dict[str, Any]],
    scale: list[dict[str, Any]],
    attendance: dict[str, int],
    teacher_comment: str | None,
    head_comment: str | None,
    next_term_starts_on: date | None,
    class_average: Decimal | None,
) -> dict[str, Any]:
    """The report card as stored at release: JSON-safe, numbers as exact decimal strings."""
    return {
        "student": {"id": str(r.student_id), "name": name},
        "class": {
            "id": str(cls.id),
            "name": cls.name,
            "school_level": cls.school_level.value,
            "class_teacher": class_teacher,
        },
        "term": {
            "id": str(term.id),
            "name": term.name,
            "code": term.code.value,
            "session_name": session_name,
            "starts_on": term.starts_on.isoformat(),
            "ends_on": term.ends_on.isoformat(),
        },
        "components": components,
        "subjects": [
            {
                "subject_id": str(line.subject_id),
                "subject_name": line.subject_name,
                "parts": {code: str(v) for code, v in line.parts.items()},
                "total": str(line.total),
                "earlier_totals": [str(v) for v in line.earlier_totals],
                "cumulative": _num(line.cumulative),
                "grade": line.grade,
                "remark": line.remark,
                "position": line.position,
                "out_of": line.out_of,
                "class_average": str(line.class_average),
                "highest": str(line.highest),
                "lowest": str(line.lowest),
            }
            for line in r.lines
        ],
        "total": str(r.total),
        "average": _num(r.average),
        "grade": r.grade,
        "remark": r.remark,
        "position": r.position,
        "out_of": r.out_of,
        "class_average": _num(class_average),
        "attendance": attendance,
        "comments": {"class_teacher": teacher_comment, "head": head_comment},
        "next_term_starts_on": next_term_starts_on.isoformat() if next_term_starts_on else None,
        "scale": scale,
    }


def _with_meta(stored: StoredReportCard) -> dict[str, Any]:
    return {
        **stored.data,
        "released": True,
        "version": stored.version,
        "released_at": stored.released_at.isoformat(),
    }


def _may_preview(actor: Principal, cls: SchoolClass) -> None:
    if (
        cls.class_teacher_id == actor.user_id
        or actor.permissions.allows(VIEW_ALL)
        or actor.permissions.allows(APPROVE)
    ):
        return
    raise PermissionDenied("For the class teacher and school leaders.")


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


async def _student(uow: UnitOfWork, tenant_id: UUID, student_id: UUID) -> None:
    user = await uow.users.get(student_id)
    if user is None or user.tenant_id != tenant_id or SchoolRole.STUDENT.value not in user.roles:
        raise NotFound("Student not found.")
