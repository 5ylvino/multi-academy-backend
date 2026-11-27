"""Assessment persistence, scoped to the current school."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.assessment.gradebook import Mark, ScoreSheet, SheetStatus
from app.domain.assessment.scheme import Band, Component


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
