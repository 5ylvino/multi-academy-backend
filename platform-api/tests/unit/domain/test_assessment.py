from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.assessment.gradebook import ScoreSheet, SheetStatus, check_score
from app.domain.assessment.scheme import (
    DEFAULT_SCALE,
    DEFAULT_SCHEME,
    Band,
    ComponentKind,
    ComponentSpec,
    check_scale,
    check_scheme,
    grade_for,
)
from app.domain.shared.errors import InvalidStateTransition, ValidationFailed

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
CA = ComponentKind.CONTINUOUS


def test_default_scheme_and_scale_are_valid() -> None:
    assert sum(s.max_score for s in check_scheme(list(DEFAULT_SCHEME))) == 100
    assert check_scale(list(DEFAULT_SCALE))[0].grade == "A"


@pytest.mark.parametrize(
    ("specs", "code"),
    [
        ([ComponentSpec("ca", "CA", CA, 40)], "scheme_not_100"),
        (
            [ComponentSpec("ca", "CA", CA, 50), ComponentSpec("ca", "CA again", CA, 50)],
            "validation_failed",
        ),
        (
            [
                ComponentSpec("e1", "Exam 1", ComponentKind.EXAM, 50),
                ComponentSpec("e2", "Exam 2", ComponentKind.EXAM, 50),
            ],
            "validation_failed",
        ),
        ([ComponentSpec("Bad Code", "CA", CA, 100)], "validation_failed"),
    ],
)
def test_scheme_rules(specs: list[ComponentSpec], code: str) -> None:
    with pytest.raises(ValidationFailed) as err:
        check_scheme(specs)
    assert err.value.code == code


def test_scale_must_cover_0_to_100() -> None:
    with pytest.raises(ValidationFailed):
        check_scale([Band("A", "Top", 50, 100), Band("F", "Fail", 0, 48)])
    with pytest.raises(ValidationFailed):
        check_scale([Band("A", "Top", 50, 99), Band("F", "Fail", 0, 49)])
    with pytest.raises(ValidationFailed):
        check_scale([Band("A", "Top", 50, 100), Band("A", "Fail", 0, 49)])
    bands = check_scale([Band("f", " Fail ", 0, 49), Band("a", "Top", 50, 100)])
    assert [(b.grade, b.remark) for b in bands] == [("A", "Top"), ("F", "Fail")]


@pytest.mark.parametrize(
    ("score", "grade"),
    [("69.49", "B"), ("69.5", "A"), ("100", "A"), ("0", "F"), ("44.5", "D"), ("39.4", "F")],
)
def test_grade_for_rounds_half_up(score: str, grade: str) -> None:
    assert grade_for(Decimal(score), list(DEFAULT_SCALE)).grade == grade


def test_check_score() -> None:
    assert check_score("7.5", 10) == Decimal("7.50")
    for bad in ("10.5", "-1", "1.234", "nan", "x"):
        with pytest.raises(ValidationFailed):
            check_score(bad, 10)


def _sheet() -> ScoreSheet:
    return ScoreSheet.open(
        tenant_id=uuid4(), term_id=uuid4(), class_id=uuid4(), subject_id=uuid4(), now=NOW
    )


def test_sheet_flow() -> None:
    teacher, leader = uuid4(), uuid4()
    sheet = _sheet()
    with pytest.raises(ValidationFailed):
        sheet.submit(by=teacher, missing=2, now=NOW)
    sheet.submit(by=teacher, missing=0, now=NOW)
    with pytest.raises(InvalidStateTransition):
        sheet.touch(NOW)
    with pytest.raises(InvalidStateTransition):
        sheet.approve(by=teacher, now=NOW)
    with pytest.raises(ValidationFailed):
        sheet.send_back(note="", now=NOW)
    sheet.send_back(note="Check Bola", now=NOW)
    assert (sheet.status, sheet.submitted_by, sheet.note) == (SheetStatus.DRAFT, None, "Check Bola")
    sheet.submit(by=teacher, missing=0, now=NOW)
    assert sheet.note is None
    with pytest.raises(InvalidStateTransition):
        sheet.recall(by=leader, now=NOW)
    sheet.recall(by=teacher, now=NOW)
    sheet.submit(by=teacher, missing=0, now=NOW)
    sheet.approve(by=leader, now=NOW)
    with pytest.raises(InvalidStateTransition):
        sheet.touch(NOW)
    with pytest.raises(InvalidStateTransition):
        sheet.send_back(note="x", now=NOW)
    sheet.reopen(note="Remarked", now=NOW)
    assert (sheet.status, sheet.approved_by) == (SheetStatus.DRAFT, None)
    with pytest.raises(InvalidStateTransition):
        sheet.reopen(note="again", now=NOW)
