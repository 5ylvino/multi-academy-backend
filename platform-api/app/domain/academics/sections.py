"""Sections: how a school groups its levels under academic heads.

A school offers one or more levels (nursery, primary, secondary). Each level belongs to exactly
one section, and each section has its own heads, teachers, classes and students. Common
arrangements:

- "Nursery and Primary" under a head teacher, and "Secondary" under a principal.
- Nursery standing on its own with its own head, primary, and secondary: three sections.
- A single-level school (only primary, or only secondary): one section.

Classes and subjects carry a level, so they belong to the section that holds that level. When a
school re-arranges its sections (splits nursery off, or merges it back), classes, subjects and
students follow their level automatically; only staff assignments need a decision.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.academics.structure import SchoolLevelCode
from app.domain.shared.entity import Entity
from app.domain.shared.errors import PermissionDenied, ValidationFailed
from app.domain.shared.ids import new_id

#: The order levels are shown in, youngest first.
LEVEL_ORDER = (SchoolLevelCode.NURSERY, SchoolLevelCode.PRIMARY, SchoolLevelCode.SECONDARY)
_LEVEL_NAMES = {
    SchoolLevelCode.NURSERY: "Nursery",
    SchoolLevelCode.PRIMARY: "Primary",
    SchoolLevelCode.SECONDARY: "Secondary",
}
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


def ordered(levels: Iterable[SchoolLevelCode]) -> list[SchoolLevelCode]:
    chosen = set(levels)
    return [lvl for lvl in LEVEL_ORDER if lvl in chosen]


def default_name(levels: Iterable[SchoolLevelCode]) -> str:
    """'Nursery and Primary', 'Secondary', 'Nursery, Primary and Secondary'."""
    names = [_LEVEL_NAMES[lvl] for lvl in ordered(levels)]
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def default_code(levels: Iterable[SchoolLevelCode]) -> str:
    return "_".join(lvl.value for lvl in ordered(levels))


class SectionFeature(StrEnum):
    """Activities a section can switch on or off on its own."""

    ATTENDANCE = "attendance"
    GRADEBOOK = "gradebook"
    REPORT_CARDS = "report_cards"
    CLINIC = "clinic"
    GATE = "gate"
    TRANSPORT = "transport"
    BOARDING = "boarding"


FEATURE_NAMES = {
    SectionFeature.ATTENDANCE: "Attendance",
    SectionFeature.GRADEBOOK: "Gradebook",
    SectionFeature.REPORT_CARDS: "Report cards",
    SectionFeature.CLINIC: "Clinic",
    SectionFeature.GATE: "Gate and pickup",
    SectionFeature.TRANSPORT: "Transport",
    SectionFeature.BOARDING: "Boarding",
}


def default_features(levels: Iterable[SchoolLevelCode]) -> frozenset[SectionFeature]:
    """Everything on, except boarding, which starts on only where there is secondary."""
    features = set(SectionFeature)
    if SchoolLevelCode.SECONDARY not in set(levels):
        features.discard(SectionFeature.BOARDING)
    return frozenset(features)


def parse_features(values: Iterable[str]) -> frozenset[SectionFeature]:
    try:
        return frozenset(SectionFeature(v) for v in values)
    except ValueError as exc:
        raise ValidationFailed("Unknown feature.", details={"field": "features"}) from exc


@dataclass(kw_only=True, eq=False)
class Section(Entity):
    tenant_id: UUID
    name: str
    code: str
    levels: frozenset[SchoolLevelCode]
    created_at: datetime
    features: frozenset[SectionFeature] = frozenset()

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        levels: Iterable[SchoolLevelCode],
        name: str | None,
        now: datetime,
    ) -> Section:
        chosen = frozenset(levels)
        if not chosen:
            raise ValidationFailed("Each section needs at least one level.", code="empty_section")
        return cls(
            id=new_id(),
            tenant_id=tenant_id,
            name=_name(name) if name else default_name(chosen),
            code=default_code(chosen),
            levels=chosen,
            created_at=now,
            features=default_features(chosen),
        )

    def rename(self, name: str) -> None:
        self.name = _name(name)

    def regroup(self, levels: Iterable[SchoolLevelCode]) -> None:
        chosen = frozenset(levels)
        if not chosen:
            raise ValidationFailed("Each section needs at least one level.", code="empty_section")
        self.levels = chosen
        self.code = default_code(chosen)

    def offers(self, feature: SectionFeature) -> bool:
        return feature in self.features

    def set_features(self, features: Iterable[SectionFeature]) -> None:
        self.features = frozenset(features)

    @property
    def has_secondary(self) -> bool:
        return SchoolLevelCode.SECONDARY in self.levels

    @property
    def has_basic(self) -> bool:
        """Nursery or primary: the levels a head teacher leads."""
        return bool(self.levels & {SchoolLevelCode.NURSERY, SchoolLevelCode.PRIMARY})


def _name(value: str) -> str:
    clean = " ".join(value.split())
    if not 1 <= len(clean) <= 60:
        raise ValidationFailed(
            "Enter a section name of up to 60 characters.", details={"field": "name"}
        )
    return clean


def default_arrangement(
    levels: Iterable[SchoolLevelCode], *, nursery_with_primary: bool = True
) -> list[frozenset[SchoolLevelCode]]:
    """The usual grouping: nursery joins primary (unless the school runs it apart), secondary
    stands alone."""
    offered = set(levels)
    groups: list[frozenset[SchoolLevelCode]] = []
    basic = offered & {SchoolLevelCode.NURSERY, SchoolLevelCode.PRIMARY}
    if nursery_with_primary and len(basic) == 2:
        groups.append(frozenset(basic))
    else:
        groups.extend(frozenset({lvl}) for lvl in ordered(basic))
    if SchoolLevelCode.SECONDARY in offered:
        groups.append(frozenset({SchoolLevelCode.SECONDARY}))
    return groups


def check_arrangement(
    groups: list[frozenset[SchoolLevelCode]], offered: frozenset[SchoolLevelCode]
) -> None:
    """Every offered level is in exactly one section, and no section is empty."""
    if not groups:
        raise ValidationFailed("Add at least one section.", code="no_sections")
    seen: set[SchoolLevelCode] = set()
    for group in groups:
        if not group:
            raise ValidationFailed("Each section needs at least one level.", code="empty_section")
        twice = seen & group
        if twice:
            raise ValidationFailed(
                f"{default_name(twice)} is in more than one section.",
                details={"levels": sorted(lvl.value for lvl in twice)},
                code="level_in_two_sections",
            )
        seen |= group
    extra = seen - offered
    if extra:
        raise ValidationFailed(
            f"Your school doesn't offer {default_name(extra)}.",
            details={"levels": sorted(lvl.value for lvl in extra)},
            code="level_not_offered",
        )
    missing = offered - seen
    if missing:
        raise ValidationFailed(
            f"Put {default_name(missing)} in a section.",
            details={"levels": sorted(lvl.value for lvl in missing)},
            code="level_without_section",
        )


@dataclass(frozen=True, slots=True)
class SectionScope:
    """The part of the school a person's academic work covers.

    Whole-school leaders (director, school admin, IT admin) cover every level. Section staff
    cover the levels of the sections they are assigned to, and nothing else.
    """

    whole_school: bool
    section_ids: frozenset[UUID]
    levels: frozenset[str]

    @classmethod
    def everything(cls) -> SectionScope:
        return cls(whole_school=True, section_ids=frozenset(), levels=frozenset())

    def covers(self, level: SchoolLevelCode | str) -> bool:
        value = level.value if isinstance(level, SchoolLevelCode) else level
        return self.whole_school or value in self.levels

    def require(self, level: SchoolLevelCode | str) -> None:
        if not self.covers(level):
            raise PermissionDenied(
                "This belongs to another section of the school.", code="other_section"
            )

    def level_filter(self) -> frozenset[str] | None:
        """None means no filter (whole school)."""
        return None if self.whole_school else self.levels
