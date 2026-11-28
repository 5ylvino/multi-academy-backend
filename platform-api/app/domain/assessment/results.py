"""Turning approved marks into results: totals, grades, positions and class statistics.

Rules (carried over from the old system and made explicit):

- A subject's term total is the sum of its parts, out of 100.
- In the second and third terms, the subject's cumulative score is the average of this term's
  total and the earlier terms' released totals in the same session. The grade comes from the
  cumulative score when there is one, otherwise from the term total.
- A student's average is the mean of their graded scores over the subjects they take.
- Positions use competition ranking: equal scores share a place and the next place is skipped
  (1, 2, 2, 4). Students not taking a subject are left out of that subject entirely.
- Every figure is rounded half up to two decimal places.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from uuid import UUID

from app.domain.assessment.scheme import Band, grade_for
from app.domain.shared.errors import InvalidStateTransition, ValidationFailed

_CENT = Decimal("0.01")


def round2(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def rank(scores: Mapping[UUID, Decimal]) -> dict[UUID, int]:
    """Competition ranking, highest first."""
    ordered = sorted(scores.values(), reverse=True)
    first_at: dict[Decimal, int] = {}
    for i, score in enumerate(ordered, start=1):
        first_at.setdefault(score, i)
    return {who: first_at[score] for who, score in scores.items()}


@dataclass(frozen=True, slots=True)
class SubjectMarks:
    """One subject's approved sheet: each taking student's marks by part code."""

    subject_id: UUID
    subject_name: str
    parts: Mapping[UUID, Mapping[str, Decimal]]  # by student; exempt students are absent


@dataclass(frozen=True, slots=True)
class SubjectLine:
    subject_id: UUID
    subject_name: str
    parts: dict[str, Decimal]
    total: Decimal
    earlier_totals: list[Decimal]
    cumulative: Decimal | None
    grade: str
    remark: str
    position: int
    out_of: int
    class_average: Decimal
    highest: Decimal
    lowest: Decimal


@dataclass(frozen=True, slots=True)
class StudentResult:
    student_id: UUID
    lines: list[SubjectLine]
    total: Decimal
    average: Decimal | None
    grade: str | None
    remark: str | None
    position: int | None
    out_of: int


@dataclass(frozen=True, slots=True)
class ClassResult:
    students: dict[UUID, StudentResult]
    class_average: Decimal | None
    subjects: list[UUID] = field(default_factory=list)


def compute(
    *,
    students: list[UUID],
    subjects: list[SubjectMarks],
    earlier: Mapping[tuple[UUID, UUID], list[Decimal]],
    bands: list[Band],
) -> ClassResult:
    """``earlier`` holds released totals from earlier terms, by (subject, student), in order."""
    lines: dict[UUID, list[SubjectLine]] = {s: [] for s in students}
    for subject in subjects:
        totals = {
            who: round2(sum(parts.values(), Decimal(0)))
            for who, parts in subject.parts.items()
            if who in lines
        }
        if not totals:
            continue
        positions = rank(totals)
        values = list(totals.values())
        average = round2(sum(values, Decimal(0)) / len(values))
        for who, total in totals.items():
            before = list(earlier.get((subject.subject_id, who), []))
            cumulative = (
                round2((total + sum(before, Decimal(0))) / (1 + len(before))) if before else None
            )
            band = grade_for(cumulative if cumulative is not None else total, bands)
            lines[who].append(
                SubjectLine(
                    subject_id=subject.subject_id,
                    subject_name=subject.subject_name,
                    parts={code: round2(v) for code, v in subject.parts[who].items()},
                    total=total,
                    earlier_totals=before,
                    cumulative=cumulative,
                    grade=band.grade,
                    remark=band.remark,
                    position=positions[who],
                    out_of=len(totals),
                    class_average=average,
                    highest=max(values),
                    lowest=min(values),
                )
            )
    averages: dict[UUID, Decimal] = {}
    for who, mine in lines.items():
        if mine:
            graded = [
                line.cumulative if line.cumulative is not None else line.total for line in mine
            ]
            averages[who] = round2(sum(graded, Decimal(0)) / len(graded))
    positions = rank(averages)
    results = {}
    for who in students:
        mine = sorted(lines[who], key=lambda line: line.subject_name)
        avg = averages.get(who)
        overall = grade_for(avg, bands) if avg is not None else None
        results[who] = StudentResult(
            student_id=who,
            lines=mine,
            total=round2(sum((line.total for line in mine), Decimal(0))),
            average=avg,
            grade=overall.grade if overall else None,
            remark=overall.remark if overall else None,
            position=positions.get(who),
            out_of=len(averages),
        )
    class_average = round2(sum(averages.values(), Decimal(0)) / len(averages)) if averages else None
    return ClassResult(
        students=results,
        class_average=class_average,
        subjects=[s.subject_id for s in subjects],
    )


# ---- release -------------------------------------------------------------------------------


class ReleaseStatus(StrEnum):
    RELEASED = "released"
    WITHDRAWN = "withdrawn"


@dataclass(kw_only=True, eq=False)
class ResultRelease:
    """A class's results for a term, published to students and guardians."""

    tenant_id: UUID
    term_id: UUID
    class_id: UUID
    status: ReleaseStatus
    version: int
    released_by: UUID
    released_at: datetime
    withdrawn_by: UUID | None = None
    withdrawn_at: datetime | None = None
    withdraw_reason: str | None = None

    @classmethod
    def first(
        cls, *, tenant_id: UUID, term_id: UUID, class_id: UUID, by: UUID, now: datetime
    ) -> ResultRelease:
        return cls(
            tenant_id=tenant_id,
            term_id=term_id,
            class_id=class_id,
            status=ReleaseStatus.RELEASED,
            version=1,
            released_by=by,
            released_at=now,
        )

    @property
    def visible(self) -> bool:
        return self.status is ReleaseStatus.RELEASED

    def release_again(self, *, by: UUID, now: datetime) -> None:
        if self.status is ReleaseStatus.RELEASED:
            raise InvalidStateTransition(
                "These results are already released.", code="already_released"
            )
        self.status = ReleaseStatus.RELEASED
        self.version += 1
        self.released_by = by
        self.released_at = now
        self.withdrawn_by = None
        self.withdrawn_at = None
        self.withdraw_reason = None

    def withdraw(self, *, by: UUID, reason: str | None, now: datetime) -> None:
        if self.status is not ReleaseStatus.RELEASED:
            raise InvalidStateTransition("These results aren't released.", code="not_released")
        text = " ".join((reason or "").split())
        if not text or len(text) > 500:
            raise ValidationFailed("Say why (up to 500 characters).", details={"field": "reason"})
        self.status = ReleaseStatus.WITHDRAWN
        self.withdrawn_by = by
        self.withdrawn_at = now
        self.withdraw_reason = text


def comment_text(value: str | None) -> str | None:
    text = " ".join((value or "").split())
    if len(text) > 500:
        raise ValidationFailed("Keep comments to 500 characters.", details={"field": "comment"})
    return text or None
