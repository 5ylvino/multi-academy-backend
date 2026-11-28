"""Assessment persistence, scoped to the current school."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from app.domain.assessment.gradebook import Mark, ScoreSheet, SheetStatus
from app.domain.assessment.results import ResultRelease
from app.domain.assessment.scheme import Band, Component


@dataclass(frozen=True, slots=True)
class Comments:
    teacher: str | None
    teacher_by: UUID | None
    head: str | None
    head_by: UUID | None


@dataclass(frozen=True, slots=True)
class StoredReportCard:
    term_id: UUID
    student_id: UUID
    class_id: UUID
    version: int
    data: dict[str, Any]
    released_at: datetime
    visible: bool  # the class's release is current (not withdrawn)


class AssessmentRepository(Protocol):
    # ---- scheme and scale -------------------------------------------------------------------

    async def components(self, level: str, *, include_inactive: bool = False) -> list[Component]:
        """In report-card order."""
        ...

    async def add_component(self, component: Component) -> None: ...

    async def save_component(self, component: Component) -> None: ...

    async def level_has_marks(self, level: str, term_id: UUID) -> bool:
        """Any mark recorded this term in a class of this level."""
        ...

    async def scale(self, level: str) -> list[Band]:
        """Highest first; empty when the school hasn't set one."""
        ...

    async def replace_scale(self, level: str, bands: list[Band]) -> None: ...

    # ---- sheets -----------------------------------------------------------------------------

    async def find_sheet(
        self, term_id: UUID, class_id: UUID, subject_id: UUID, *, for_update: bool = False
    ) -> ScoreSheet | None: ...

    async def get_sheet(self, sheet_id: UUID, *, for_update: bool = False) -> ScoreSheet | None: ...

    async def add_sheet(self, sheet: ScoreSheet) -> None: ...

    async def save_sheet(self, sheet: ScoreSheet) -> None: ...

    async def sheets(
        self,
        *,
        term_id: UUID,
        class_id: UUID | None = None,
        status: SheetStatus | None = None,
        for_update: bool = False,
    ) -> list[ScoreSheet]: ...

    async def marks(self, sheet_id: UUID) -> list[Mark]: ...

    async def marks_for_sheets(self, sheet_ids: list[UUID]) -> dict[UUID, list[Mark]]: ...

    async def save_marks(
        self, sheet_id: UUID, marks: list[Mark], *, by: UUID, now: datetime
    ) -> None:
        """Insert or replace each mark."""
        ...

    async def clear_marks(self, sheet_id: UUID, keys: list[tuple[UUID, UUID]]) -> None:
        """Remove the (student, part) marks given."""
        ...

    async def exemptions(self, sheet_id: UUID) -> set[UUID]: ...

    async def exemptions_for_sheets(self, sheet_ids: list[UUID]) -> dict[UUID, set[UUID]]: ...

    async def set_exempt(self, sheet_id: UUID, student_id: UUID, *, exempt: bool) -> None: ...

    # ---- comments, releases and report cards ------------------------------------------------

    async def comments(self, term_id: UUID, student_ids: set[UUID]) -> dict[UUID, Comments]: ...

    async def save_comment(
        self,
        term_id: UUID,
        student_id: UUID,
        *,
        teacher: tuple[str | None, UUID] | None,
        head: tuple[str | None, UUID] | None,
        now: datetime,
    ) -> None:
        """Set the class teacher's or the head's comment (whichever is given)."""
        ...

    async def get_release(
        self, term_id: UUID, class_id: UUID, *, for_update: bool = False
    ) -> ResultRelease | None: ...

    async def add_release(self, release: ResultRelease) -> None: ...

    async def save_release(self, release: ResultRelease) -> None: ...

    async def save_report_cards(self, cards: list[StoredReportCard]) -> None:
        """Insert or replace each student's card for the term."""
        ...

    async def report_card(self, term_id: UUID, student_id: UUID) -> StoredReportCard | None: ...

    async def report_cards_of(self, student_id: UUID) -> list[StoredReportCard]:
        """Visible cards, newest term first."""
        ...

    async def released_cards(
        self, term_ids: list[UUID], student_ids: set[UUID]
    ) -> list[StoredReportCard]:
        """Visible cards for these terms and students (for cumulative scores)."""
        ...
