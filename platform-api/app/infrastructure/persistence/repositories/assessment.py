"""Assessment scheme, grading bands, score sheets, marks and exemptions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, delete, exists, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.assessment.gradebook import Mark, ScoreSheet, SheetStatus
from app.domain.assessment.repositories import Comments, StoredReportCard
from app.domain.assessment.results import ReleaseStatus, ResultRelease
from app.domain.assessment.scheme import Band, Component, ComponentKind
from app.infrastructure.persistence.models.academics import SchoolClassRow, TermRow
from app.infrastructure.persistence.models.assessment import (
    BandRow,
    CommentRow,
    ComponentRow,
    ExemptionRow,
    MarkRow,
    ReleaseRow,
    ReportCardRow,
    SheetRow,
)
from app.infrastructure.persistence.repositories.scope_state import ScopeState


def _component(r: ComponentRow) -> Component:
    return Component(
        id=r.id,
        tenant_id=r.tenant_id,
        school_level=r.school_level,
        code=r.code,
        name=r.name,
        kind=ComponentKind(r.kind),
        max_score=r.max_score,
        position=r.position,
        active=r.active,
    )


def _sheet(r: SheetRow) -> ScoreSheet:
    return ScoreSheet(
        id=r.id,
        tenant_id=r.tenant_id,
        term_id=r.term_id,
        class_id=r.class_id,
        subject_id=r.subject_id,
        status=SheetStatus(r.status),
        updated_at=r.updated_at,
        submitted_by=r.submitted_by,
        submitted_at=r.submitted_at,
        approved_by=r.approved_by,
        approved_at=r.approved_at,
        note=r.note,
    )


def _release(r: ReleaseRow) -> ResultRelease:
    return ResultRelease(
        tenant_id=r.tenant_id,
        term_id=r.term_id,
        class_id=r.class_id,
        status=ReleaseStatus(r.status),
        version=r.version,
        released_by=r.released_by,
        released_at=r.released_at,
        withdrawn_by=r.withdrawn_by,
        withdrawn_at=r.withdrawn_at,
        withdraw_reason=r.withdraw_reason,
    )


def _card(r: ReportCardRow, status: str) -> StoredReportCard:
    return StoredReportCard(
        term_id=r.term_id,
        student_id=r.student_id,
        class_id=r.class_id,
        version=r.version,
        data=r.data,
        released_at=r.released_at,
        visible=status == ReleaseStatus.RELEASED.value,
    )


class SqlAssessmentRepository:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    @property
    def _tid(self) -> UUID:
        if self._scope.tenant_id is None:
            raise RuntimeError("Assessment needs a tenant scope.")
        return self._scope.tenant_id

    # ---- scheme and scale -------------------------------------------------------------------

    async def components(self, level: str, *, include_inactive: bool = False) -> list[Component]:
        stmt = select(ComponentRow).where(
            ComponentRow.tenant_id == self._tid, ComponentRow.school_level == level
        )
        if not include_inactive:
            stmt = stmt.where(ComponentRow.active.is_(True))
        rows = await self._s.scalars(stmt.order_by(ComponentRow.position, ComponentRow.code))
        return [_component(r) for r in rows]

    async def add_component(self, component: Component) -> None:
        c = component
        await self._s.execute(
            pg_insert(ComponentRow).values(
                tenant_id=self._tid,
                id=c.id,
                school_level=c.school_level,
                code=c.code,
                name=c.name,
                kind=c.kind.value,
                max_score=c.max_score,
                position=c.position,
                active=c.active,
            )
        )

    async def save_component(self, component: Component) -> None:
        c = component
        await self._s.execute(
            update(ComponentRow)
            .where(ComponentRow.tenant_id == self._tid, ComponentRow.id == c.id)
            .values(
                name=c.name,
                kind=c.kind.value,
                max_score=c.max_score,
                position=c.position,
                active=c.active,
            )
        )

    async def level_has_marks(self, level: str, term_id: UUID) -> bool:
        found = await self._s.scalar(
            select(
                exists().where(
                    MarkRow.tenant_id == self._tid,
                    SheetRow.tenant_id == MarkRow.tenant_id,
                    SheetRow.id == MarkRow.sheet_id,
                    SheetRow.term_id == term_id,
                    SchoolClassRow.tenant_id == SheetRow.tenant_id,
                    SchoolClassRow.id == SheetRow.class_id,
                    SchoolClassRow.school_level == level,
                )
            )
        )
        return bool(found)

    async def scale(self, level: str) -> list[Band]:
        rows = await self._s.scalars(
            select(BandRow)
            .where(BandRow.tenant_id == self._tid, BandRow.school_level == level)
            .order_by(BandRow.min_score.desc())
        )
        return [
            Band(grade=r.grade, remark=r.remark, min_score=r.min_score, max_score=r.max_score)
            for r in rows
        ]

    async def replace_scale(self, level: str, bands: list[Band]) -> None:
        await self._s.execute(
            delete(BandRow).where(BandRow.tenant_id == self._tid, BandRow.school_level == level)
        )
        await self._s.execute(
            pg_insert(BandRow),
            [
                {
                    "tenant_id": self._tid,
                    "school_level": level,
                    "grade": b.grade,
                    "remark": b.remark,
                    "min_score": b.min_score,
                    "max_score": b.max_score,
                }
                for b in bands
            ],
        )

    # ---- sheets -----------------------------------------------------------------------------

    async def find_sheet(
        self, term_id: UUID, class_id: UUID, subject_id: UUID, *, for_update: bool = False
    ) -> ScoreSheet | None:
        stmt = select(SheetRow).where(
            SheetRow.tenant_id == self._tid,
            SheetRow.term_id == term_id,
            SheetRow.class_id == class_id,
            SheetRow.subject_id == subject_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _sheet(row) if row else None

    async def get_sheet(self, sheet_id: UUID, *, for_update: bool = False) -> ScoreSheet | None:
        stmt = select(SheetRow).where(SheetRow.tenant_id == self._tid, SheetRow.id == sheet_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _sheet(row) if row else None

    async def add_sheet(self, sheet: ScoreSheet) -> None:
        await self._s.execute(
            pg_insert(SheetRow).values(
                tenant_id=self._tid,
                id=sheet.id,
                term_id=sheet.term_id,
                class_id=sheet.class_id,
                subject_id=sheet.subject_id,
                status=sheet.status.value,
                updated_at=sheet.updated_at,
            )
        )

    async def save_sheet(self, sheet: ScoreSheet) -> None:
        s = sheet
        await self._s.execute(
            update(SheetRow)
            .where(SheetRow.tenant_id == self._tid, SheetRow.id == s.id)
            .values(
                status=s.status.value,
                updated_at=s.updated_at,
                submitted_by=s.submitted_by,
                submitted_at=s.submitted_at,
                approved_by=s.approved_by,
                approved_at=s.approved_at,
                note=s.note,
            )
        )

    async def sheets(
        self,
        *,
        term_id: UUID,
        class_id: UUID | None = None,
        status: SheetStatus | None = None,
        for_update: bool = False,
    ) -> list[ScoreSheet]:
        stmt = select(SheetRow).where(SheetRow.tenant_id == self._tid, SheetRow.term_id == term_id)
        if class_id is not None:
            stmt = stmt.where(SheetRow.class_id == class_id)
        if status is not None:
            stmt = stmt.where(SheetRow.status == status.value)
        stmt = stmt.order_by(SheetRow.id)
        if for_update:
            stmt = stmt.with_for_update()
        return [_sheet(r) for r in await self._s.scalars(stmt)]

    async def marks(self, sheet_id: UUID) -> list[Mark]:
        return (await self.marks_for_sheets([sheet_id])).get(sheet_id, [])

    async def marks_for_sheets(self, sheet_ids: list[UUID]) -> dict[UUID, list[Mark]]:
        if not sheet_ids:
            return {}
        rows = await self._s.scalars(
            select(MarkRow).where(MarkRow.tenant_id == self._tid, MarkRow.sheet_id.in_(sheet_ids))
        )
        found: dict[UUID, list[Mark]] = {}
        for r in rows:
            found.setdefault(r.sheet_id, []).append(
                Mark(student_id=r.student_id, component_id=r.component_id, score=r.score)
            )
        return found

    async def save_marks(
        self, sheet_id: UUID, marks: list[Mark], *, by: UUID, now: datetime
    ) -> None:
        if not marks:
            return
        stmt = pg_insert(MarkRow).values(
            [
                {
                    "tenant_id": self._tid,
                    "sheet_id": sheet_id,
                    "student_id": m.student_id,
                    "component_id": m.component_id,
                    "score": m.score,
                    "entered_by": by,
                    "entered_at": now,
                }
                for m in marks
            ]
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    MarkRow.tenant_id,
                    MarkRow.sheet_id,
                    MarkRow.student_id,
                    MarkRow.component_id,
                ],
                set_={
                    "score": stmt.excluded.score,
                    "entered_by": stmt.excluded.entered_by,
                    "entered_at": stmt.excluded.entered_at,
                },
                where=MarkRow.score.is_distinct_from(stmt.excluded.score),
            )
        )

    async def clear_marks(self, sheet_id: UUID, keys: list[tuple[UUID, UUID]]) -> None:
        if not keys:
            return
        await self._s.execute(
            delete(MarkRow).where(
                MarkRow.tenant_id == self._tid,
                MarkRow.sheet_id == sheet_id,
                tuple_(MarkRow.student_id, MarkRow.component_id).in_(keys),
            )
        )

    async def exemptions(self, sheet_id: UUID) -> set[UUID]:
        return (await self.exemptions_for_sheets([sheet_id])).get(sheet_id, set())

    async def exemptions_for_sheets(self, sheet_ids: list[UUID]) -> dict[UUID, set[UUID]]:
        if not sheet_ids:
            return {}
        rows = await self._s.execute(
            select(ExemptionRow.sheet_id, ExemptionRow.student_id).where(
                ExemptionRow.tenant_id == self._tid, ExemptionRow.sheet_id.in_(sheet_ids)
            )
        )
        found: dict[UUID, set[UUID]] = {}
        for sheet_id, student_id in rows.all():
            found.setdefault(sheet_id, set()).add(student_id)
        return found

    async def set_exempt(self, sheet_id: UUID, student_id: UUID, *, exempt: bool) -> None:
        if exempt:
            await self._s.execute(
                pg_insert(ExemptionRow)
                .values(tenant_id=self._tid, sheet_id=sheet_id, student_id=student_id)
                .on_conflict_do_nothing()
            )
        else:
            await self._s.execute(
                delete(ExemptionRow).where(
                    ExemptionRow.tenant_id == self._tid,
                    ExemptionRow.sheet_id == sheet_id,
                    ExemptionRow.student_id == student_id,
                )
            )

    # ---- comments, releases and report cards ------------------------------------------------

    async def comments(self, term_id: UUID, student_ids: set[UUID]) -> dict[UUID, Comments]:
        if not student_ids:
            return {}
        rows = await self._s.scalars(
            select(CommentRow).where(
                CommentRow.tenant_id == self._tid,
                CommentRow.term_id == term_id,
                CommentRow.student_id.in_(student_ids),
            )
        )
        return {
            r.student_id: Comments(
                teacher=r.teacher_comment,
                teacher_by=r.teacher_by,
                head=r.head_comment,
                head_by=r.head_by,
            )
            for r in rows
        }

    async def save_comment(
        self,
        term_id: UUID,
        student_id: UUID,
        *,
        teacher: tuple[str | None, UUID] | None,
        head: tuple[str | None, UUID] | None,
        now: datetime,
    ) -> None:
        values: dict[str, object] = {"updated_at": now}
        if teacher is not None:
            values |= {"teacher_comment": teacher[0], "teacher_by": teacher[1]}
        if head is not None:
            values |= {"head_comment": head[0], "head_by": head[1]}
        stmt = pg_insert(CommentRow).values(
            tenant_id=self._tid, term_id=term_id, student_id=student_id, **values
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[CommentRow.tenant_id, CommentRow.term_id, CommentRow.student_id],
                set_=values,
            )
        )

    async def get_release(
        self, term_id: UUID, class_id: UUID, *, for_update: bool = False
    ) -> ResultRelease | None:
        stmt = select(ReleaseRow).where(
            ReleaseRow.tenant_id == self._tid,
            ReleaseRow.term_id == term_id,
            ReleaseRow.class_id == class_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _release(row) if row else None

    async def add_release(self, release: ResultRelease) -> None:
        r = release
        await self._s.execute(
            pg_insert(ReleaseRow).values(
                tenant_id=self._tid,
                term_id=r.term_id,
                class_id=r.class_id,
                status=r.status.value,
                version=r.version,
                released_by=r.released_by,
                released_at=r.released_at,
            )
        )

    async def save_release(self, release: ResultRelease) -> None:
        r = release
        await self._s.execute(
            update(ReleaseRow)
            .where(
                ReleaseRow.tenant_id == self._tid,
                ReleaseRow.term_id == r.term_id,
                ReleaseRow.class_id == r.class_id,
            )
            .values(
                status=r.status.value,
                version=r.version,
                released_by=r.released_by,
                released_at=r.released_at,
                withdrawn_by=r.withdrawn_by,
                withdrawn_at=r.withdrawn_at,
                withdraw_reason=r.withdraw_reason,
            )
        )

    async def save_report_cards(self, cards: list[StoredReportCard]) -> None:
        if not cards:
            return
        stmt = pg_insert(ReportCardRow).values(
            [
                {
                    "tenant_id": self._tid,
                    "term_id": c.term_id,
                    "student_id": c.student_id,
                    "class_id": c.class_id,
                    "version": c.version,
                    "data": c.data,
                    "released_at": c.released_at,
                }
                for c in cards
            ]
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    ReportCardRow.tenant_id,
                    ReportCardRow.term_id,
                    ReportCardRow.student_id,
                ],
                set_={
                    "class_id": stmt.excluded.class_id,
                    "version": stmt.excluded.version,
                    "data": stmt.excluded.data,
                    "released_at": stmt.excluded.released_at,
                },
            )
        )

    def _cards(self) -> Select[ReportCardRow, str]:
        return select(ReportCardRow, ReleaseRow.status).join(
            ReleaseRow,
            (ReleaseRow.tenant_id == ReportCardRow.tenant_id)
            & (ReleaseRow.term_id == ReportCardRow.term_id)
            & (ReleaseRow.class_id == ReportCardRow.class_id)
            # A card left from an earlier version (the student has since left the class) is stale.
            & (ReleaseRow.version == ReportCardRow.version),
        )

    async def report_card(self, term_id: UUID, student_id: UUID) -> StoredReportCard | None:
        row = (
            await self._s.execute(
                self._cards().where(
                    ReportCardRow.tenant_id == self._tid,
                    ReportCardRow.term_id == term_id,
                    ReportCardRow.student_id == student_id,
                )
            )
        ).first()
        return _card(row[0], row[1]) if row else None

    async def report_cards_of(self, student_id: UUID) -> list[StoredReportCard]:
        rows = await self._s.execute(
            self._cards()
            .join(
                TermRow,
                (TermRow.tenant_id == ReportCardRow.tenant_id)
                & (TermRow.id == ReportCardRow.term_id),
            )
            .where(
                ReportCardRow.tenant_id == self._tid,
                ReportCardRow.student_id == student_id,
                ReleaseRow.status == ReleaseStatus.RELEASED.value,
            )
            .order_by(TermRow.starts_on.desc())
        )
        return [_card(r[0], r[1]) for r in rows.all()]

    async def released_cards(
        self, term_ids: list[UUID], student_ids: set[UUID]
    ) -> list[StoredReportCard]:
        if not term_ids or not student_ids:
            return []
        rows = await self._s.execute(
            self._cards().where(
                ReportCardRow.tenant_id == self._tid,
                ReportCardRow.term_id.in_(term_ids),
                ReportCardRow.student_id.in_(student_ids),
                ReleaseRow.status == ReleaseStatus.RELEASED.value,
            )
        )
        return [_card(r[0], r[1]) for r in rows.all()]
