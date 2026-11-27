"""The gradebook: one score sheet per class, subject and term.

The subject teacher fills in each student's marks for each part of the scheme and submits the
sheet when it is complete. A school leader other than the person who submitted it approves it,
which locks it, or sends it back with a reason. An approved sheet can be reopened, with a
reason, until the class's results are released.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID

from app.domain.shared.errors import InvalidStateTransition, ValidationFailed
from app.domain.shared.ids import new_id


class SheetStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"


def check_score(value: Decimal | float | str, max_score: int) -> Decimal:
    """A mark between 0 and the part's maximum, to at most two decimal places."""
    try:
        score = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValidationFailed("Enter a number.", details={"field": "score"}) from exc
    if not score.is_finite() or score != score.quantize(Decimal("0.01")):
        raise ValidationFailed("Use at most two decimal places.", details={"field": "score"})
    if not Decimal(0) <= score <= Decimal(max_score):
        raise ValidationFailed(
            f"Marks for this part are from 0 to {max_score}.",
            details={"field": "score", "max": max_score},
            code="score_out_of_range",
        )
    return score.quantize(Decimal("0.01"))


def _reason(note: str | None) -> str:
    text = " ".join((note or "").split())
    if not text:
        raise ValidationFailed("Say why, for the teacher.", details={"field": "note"})
    if len(text) > 500:
        raise ValidationFailed("That is too long.", details={"field": "note"})
    return text


@dataclass(frozen=True, slots=True)
class Mark:
    student_id: UUID
    component_id: UUID
    score: Decimal


@dataclass(kw_only=True, eq=False)
class ScoreSheet:
    id: UUID
    tenant_id: UUID
    term_id: UUID
    class_id: UUID
    subject_id: UUID
    status: SheetStatus
    updated_at: datetime
    submitted_by: UUID | None = None
    submitted_at: datetime | None = None
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    note: str | None = None

    @classmethod
    def open(
        cls, *, tenant_id: UUID, term_id: UUID, class_id: UUID, subject_id: UUID, now: datetime
    ) -> ScoreSheet:
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            term_id=term_id,
            class_id=class_id,
            subject_id=subject_id,
            status=SheetStatus.DRAFT,
            updated_at=now,
        )

    def ensure_editable(self) -> None:
        if self.status is SheetStatus.SUBMITTED:
            raise InvalidStateTransition(
                "This sheet is waiting for approval. Ask for it back to make changes.",
                code="sheet_submitted",
            )
        if self.status is SheetStatus.APPROVED:
            raise InvalidStateTransition(
                "This sheet is approved. A school leader can reopen it.", code="sheet_approved"
            )

    def touch(self, now: datetime) -> None:
        self.ensure_editable()
        self.updated_at = now

    def submit(self, *, by: UUID, missing: int, now: datetime) -> None:
        self.ensure_editable()
        if missing:
            raise ValidationFailed(
                f"{missing} marks are still empty. Enter 0 for work not done, or mark the "
                "student as not taking this subject.",
                details={"missing": missing},
                code="sheet_incomplete",
            )
        self.status = SheetStatus.SUBMITTED
        self.submitted_by = by
        self.submitted_at = now
        self.note = None
        self.updated_at = now

    def approve(self, *, by: UUID, now: datetime) -> None:
        if self.status is not SheetStatus.SUBMITTED:
            raise InvalidStateTransition(
                "Only a submitted sheet can be approved.", code="not_submitted"
            )
        if by == self.submitted_by:
            raise InvalidStateTransition(
                "Someone other than the person who submitted it must approve it.",
                code="self_approval",
            )
        self.status = SheetStatus.APPROVED
        self.approved_by = by
        self.approved_at = now
        self.updated_at = now

    def send_back(self, *, note: str | None, now: datetime) -> None:
        if self.status is not SheetStatus.SUBMITTED:
            raise InvalidStateTransition(
                "Only a submitted sheet can be sent back.", code="not_submitted"
            )
        self.note = _reason(note)
        self._to_draft(now)

    def reopen(self, *, note: str | None, now: datetime) -> None:
        if self.status is not SheetStatus.APPROVED:
            raise InvalidStateTransition(
                "Only an approved sheet can be reopened.", code="not_approved"
            )
        self.note = _reason(note)
        self._to_draft(now)

    def recall(self, *, by: UUID, now: datetime) -> None:
        """The teacher takes back a sheet they submitted, before anyone decides on it."""
        if self.status is not SheetStatus.SUBMITTED:
            raise InvalidStateTransition(
                "This sheet isn't waiting for approval.", code="not_submitted"
            )
        if by != self.submitted_by:
            raise InvalidStateTransition("Only the person who submitted it can take it back.")
        self.note = None
        self._to_draft(now)

    def _to_draft(self, now: datetime) -> None:
        self.status = SheetStatus.DRAFT
        self.submitted_by = None
        self.submitted_at = None
        self.approved_by = None
        self.approved_at = None
        self.updated_at = now
