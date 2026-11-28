"""How a subject is scored each term, and how a score becomes a grade.

Each school level (nursery, primary, secondary) has an assessment scheme: the parts that make
up a subject's mark for the term, such as a note, two tests and the examination, each with the
most it can score. The parts always add up to 100, so a subject's term total is a percentage.

Each level also has a grading scale: bands of whole-number totals with a grade and a remark,
covering 0 to 100 with no gaps or overlaps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from uuid import UUID

from app.domain.shared.errors import ValidationFailed
from app.domain.shared.ids import new_id

MAX_COMPONENTS = 8
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


class ComponentKind(StrEnum):
    CONTINUOUS = "continuous"  # continuous assessment: notes, tests, projects
    EXAM = "exam"


@dataclass(kw_only=True, eq=False)
class Component:
    id: UUID
    tenant_id: UUID
    school_level: str
    code: str
    name: str
    kind: ComponentKind
    max_score: int
    position: int
    active: bool = True


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    code: str
    name: str
    kind: ComponentKind
    max_score: int


def check_scheme(specs: list[ComponentSpec]) -> list[ComponentSpec]:
    """Validate a whole scheme. Returns it with names tidied, in the order given."""
    if not 1 <= len(specs) <= MAX_COMPONENTS:
        raise ValidationFailed(
            f"Use between 1 and {MAX_COMPONENTS} parts.", details={"field": "components"}
        )
    seen: set[str] = set()
    clean: list[ComponentSpec] = []
    for spec in specs:
        code = spec.code.strip().lower()
        if not _CODE.match(code):
            raise ValidationFailed(
                "Codes use lower-case letters, digits and underscores.",
                details={"field": "code", "code": spec.code},
            )
        if code in seen:
            raise ValidationFailed("Each part needs its own code.", details={"code": code})
        seen.add(code)
        name = " ".join(spec.name.split())
        if not 1 <= len(name) <= 40:
            raise ValidationFailed("Name each part (up to 40 characters).", details={"code": code})
        if not 1 <= spec.max_score <= 100:
            raise ValidationFailed("Each part scores between 1 and 100.", details={"code": code})
        clean.append(ComponentSpec(code=code, name=name, kind=spec.kind, max_score=spec.max_score))
    total = sum(s.max_score for s in clean)
    if total != 100:
        raise ValidationFailed(
            f"The parts must add up to 100 (they add up to {total}).",
            details={"field": "components"},
            code="scheme_not_100",
        )
    if sum(1 for s in clean if s.kind is ComponentKind.EXAM) > 1:
        raise ValidationFailed("Use one examination part.", details={"field": "components"})
    return clean


#: Matches the old system's default: 40 continuous assessment, 60 examination.
DEFAULT_SCHEME = (
    ComponentSpec(code="note", name="Note", kind=ComponentKind.CONTINUOUS, max_score=10),
    ComponentSpec(code="test_1", name="First test", kind=ComponentKind.CONTINUOUS, max_score=15),
    ComponentSpec(code="test_2", name="Second test", kind=ComponentKind.CONTINUOUS, max_score=15),
    ComponentSpec(code="exam", name="Examination", kind=ComponentKind.EXAM, max_score=60),
)


def new_component(
    *, tenant_id: UUID, school_level: str, spec: ComponentSpec, position: int
) -> Component:
    return Component(
        id=new_id(),
        tenant_id=tenant_id,
        school_level=school_level,
        code=spec.code,
        name=spec.name,
        kind=spec.kind,
        max_score=spec.max_score,
        position=position,
    )


# ---- grading -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Band:
    grade: str
    remark: str
    min_score: int
    max_score: int


DEFAULT_SCALE = (
    Band(grade="A", remark="Distinction", min_score=70, max_score=100),
    Band(grade="B", remark="Very good", min_score=60, max_score=69),
    Band(grade="C", remark="Credit", min_score=50, max_score=59),
    Band(grade="D", remark="Pass", min_score=45, max_score=49),
    Band(grade="E", remark="Weak pass", min_score=40, max_score=44),
    Band(grade="F", remark="Fail", min_score=0, max_score=39),
)


def check_scale(bands: list[Band]) -> list[Band]:
    """Bands must cover 0 to 100 in whole numbers with no gaps or overlaps. Highest first."""
    if not 2 <= len(bands) <= 12:
        raise ValidationFailed("Use between 2 and 12 grades.", details={"field": "bands"})
    clean = []
    for b in bands:
        grade = b.grade.strip().upper()
        remark = " ".join(b.remark.split())
        if not 1 <= len(grade) <= 3 or not 1 <= len(remark) <= 40:
            raise ValidationFailed(
                "Each grade needs a short label and a remark.", details={"grade": b.grade}
            )
        if not 0 <= b.min_score <= b.max_score <= 100:
            raise ValidationFailed("Check the score range.", details={"grade": grade})
        clean.append(Band(grade=grade, remark=remark, min_score=b.min_score, max_score=b.max_score))
    if len({b.grade for b in clean}) != len(clean):
        raise ValidationFailed("Each grade label can be used once.", details={"field": "bands"})
    ordered = sorted(clean, key=lambda b: b.min_score)
    expected = 0
    for b in ordered:
        if b.min_score != expected:
            raise ValidationFailed(
                f"The grades must cover every score from 0 to 100; check around {expected}.",
                details={"field": "bands"},
                code="scale_gap",
            )
        expected = b.max_score + 1
    if expected != 101:
        raise ValidationFailed(
            "The top grade must end at 100.", details={"field": "bands"}, code="scale_gap"
        )
    return list(reversed(ordered))


def grade_for(score: Decimal, bands: list[Band]) -> Band:
    """The band for a total. Totals are rounded half up to a whole number first (69.5 is 70)."""
    whole = int(score.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    for band in bands:
        if band.min_score <= whole <= band.max_score:
            return band
    raise ValueError(f"No band covers {whole}")  # pragma: no cover - scales cover 0..100
