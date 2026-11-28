"""School sections: grouping levels, checking the owner's arrangement, features and scope."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.academics.sections import (
    Section,
    SectionFeature,
    SectionScope,
    check_arrangement,
    default_arrangement,
    default_code,
    default_features,
    default_name,
    parse_features,
)
from app.domain.academics.structure import SchoolLevelCode
from app.domain.shared.errors import PermissionDenied, ValidationFailed

N, P, S = SchoolLevelCode.NURSERY, SchoolLevelCode.PRIMARY, SchoolLevelCode.SECONDARY
NOW = datetime(2026, 11, 28, tzinfo=UTC)


@pytest.mark.parametrize(
    ("levels", "together", "expected"),
    [
        ({N}, True, [{N}]),
        ({P}, True, [{P}]),
        ({S}, True, [{S}]),
        ({N, P}, True, [{N, P}]),
        ({N, P}, False, [{N}, {P}]),
        ({P, S}, True, [{P}, {S}]),
        ({N, P, S}, True, [{N, P}, {S}]),
        ({N, P, S}, False, [{N}, {P}, {S}]),
    ],
)
def test_default_arrangement(
    levels: set[SchoolLevelCode], together: bool, expected: list[set[SchoolLevelCode]]
) -> None:
    groups = default_arrangement(levels, nursery_with_primary=together)
    assert groups == [frozenset(g) for g in expected]
    check_arrangement(groups, frozenset(levels))


def test_names_and_codes_follow_level_order() -> None:
    assert default_name({P, N}) == "Nursery and Primary"
    assert default_name({S}) == "Secondary"
    assert default_code({P, N}) == default_code({N, P})
    assert default_code({N}) != default_code({P})


@pytest.mark.parametrize(
    ("groups", "code"),
    [
        ([], "no_sections"),
        ([frozenset()], "empty_section"),
        ([frozenset({P}), frozenset({P, S})], "level_in_two_sections"),
        ([frozenset({P}), frozenset({S}), frozenset({N})], "level_not_offered"),
        ([frozenset({P})], "level_without_section"),
    ],
)
def test_bad_arrangements_are_refused(groups: list[frozenset[SchoolLevelCode]], code: str) -> None:
    with pytest.raises(ValidationFailed) as err:
        check_arrangement(groups, frozenset({P, S}))
    assert err.value.code == code


def test_boarding_starts_on_only_with_secondary() -> None:
    assert SectionFeature.BOARDING not in default_features({N, P})
    assert SectionFeature.BOARDING in default_features({S})
    assert default_features({S}) == frozenset(SectionFeature)


def test_parse_features_rejects_unknown_names() -> None:
    assert parse_features(["gate", "clinic"]) == {SectionFeature.GATE, SectionFeature.CLINIC}
    with pytest.raises(ValidationFailed):
        parse_features(["gate", "swimming"])


def test_section_lifecycle() -> None:
    section = Section.create(tenant_id=uuid4(), levels={N, P}, name=None, now=NOW)
    assert section.name == "Nursery and Primary"
    assert section.has_basic
    assert not section.has_secondary
    assert not section.offers(SectionFeature.BOARDING)

    section.rename("  Junior   School ")
    assert section.name == "Junior School"
    with pytest.raises(ValidationFailed):
        section.rename("x" * 61)

    section.regroup({S})
    assert section.has_secondary
    assert not section.has_basic
    assert section.code == default_code({S})
    with pytest.raises(ValidationFailed) as err:
        section.regroup(set())
    assert err.value.code == "empty_section"

    section.set_features({SectionFeature.ATTENDANCE})
    assert section.offers(SectionFeature.ATTENDANCE)
    assert not section.offers(SectionFeature.GRADEBOOK)

    with pytest.raises(ValidationFailed):
        Section.create(tenant_id=uuid4(), levels=set(), name=None, now=NOW)


def test_scope() -> None:
    everything = SectionScope.everything()
    assert everything.covers(S)
    assert everything.covers("nursery")
    assert everything.level_filter() is None

    primary = SectionScope(
        whole_school=False, section_ids=frozenset({uuid4()}), levels=frozenset({"primary"})
    )
    assert primary.covers(P)
    assert primary.covers("primary")
    assert not primary.covers(S)
    assert primary.level_filter() == {"primary"}
    primary.require(P)
    with pytest.raises(PermissionDenied) as err:
        primary.require(S)
    assert err.value.code == "other_section"

    nobody = SectionScope(whole_school=False, section_ids=frozenset(), levels=frozenset())
    assert not nobody.covers(N)
    assert nobody.level_filter() == frozenset()
