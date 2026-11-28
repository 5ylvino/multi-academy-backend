"""Results rules: totals, cumulative scores, competition ranking, and the release lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.assessment.results import (
    ReleaseStatus,
    ResultRelease,
    SubjectMarks,
    comment_text,
    compute,
    rank,
    round2,
)
from app.domain.assessment.scheme import DEFAULT_SCALE
from app.domain.shared.errors import InvalidStateTransition, ValidationFailed

NOW = datetime(2026, 11, 28, 9, tzinfo=UTC)
D = Decimal


def _marks(note: str, exam: str) -> dict[str, Decimal]:
    return {"note": D(note), "exam": D(exam)}


def test_round_half_up() -> None:
    assert round2(D("74.125")) == D("74.13")
    assert round2(D("2.005")) == D("2.01")


def test_competition_ranking_shares_places_and_skips() -> None:
    a, b, c, d = uuid4(), uuid4(), uuid4(), uuid4()
    assert rank({a: D(90), b: D(80), c: D(80), d: D(70)}) == {a: 1, b: 2, c: 2, d: 4}


def test_compute_totals_positions_and_exempt_students() -> None:
    ada, bola, chidi = uuid4(), uuid4(), uuid4()
    mth, eng = uuid4(), uuid4()
    result = compute(
        students=[ada, bola, chidi],
        subjects=[
            SubjectMarks(
                subject_id=mth,
                subject_name="Mathematics",
                parts={ada: _marks("8", "76"), bola: _marks("6", "52"), chidi: _marks("9", "73")},
            ),
            # Chidi doesn't take English: he is left out of its ranking.
            SubjectMarks(
                subject_id=eng,
                subject_name="English",
                parts={ada: _marks("5", "60"), bola: _marks("9", "83")},
            ),
        ],
        earlier={},
        bands=list(DEFAULT_SCALE),
    )
    a, b, c = (result.students[s] for s in (ada, bola, chidi))
    english = next(line for line in a.lines if line.subject_name == "English")
    assert (english.total, english.position, english.out_of, english.grade) == (D(65), 2, 2, "B")
    assert (english.highest, english.lowest, english.class_average) == (D(92), D(65), D("78.50"))
    assert [line.subject_name for line in a.lines] == ["English", "Mathematics"]
    assert (a.average, b.average, c.average) == (D("74.50"), D("75.00"), D("82.00"))
    assert (c.position, b.position, a.position) == (1, 2, 3)
    assert len(c.lines) == 1
    assert result.class_average == D("77.17")


def test_cumulative_grade_uses_earlier_terms() -> None:
    ada, mth = uuid4(), uuid4()
    result = compute(
        students=[ada],
        subjects=[
            SubjectMarks(subject_id=mth, subject_name="Mathematics", parts={ada: _marks("5", "40")})
        ],
        earlier={(mth, ada): [D(80), D(71)]},
        bands=list(DEFAULT_SCALE),
    )
    line = result.students[ada].lines[0]
    assert (line.total, line.cumulative, line.grade) == (D(45), D("65.33"), "B")
    assert result.students[ada].average == D("65.33")


def test_student_with_no_marks_has_no_position() -> None:
    ada = uuid4()
    result = compute(students=[ada], subjects=[], earlier={}, bands=list(DEFAULT_SCALE))
    assert (result.students[ada].position, result.students[ada].average) == (None, None)
    assert result.class_average is None


def test_release_lifecycle() -> None:
    by = uuid4()
    release = ResultRelease.first(
        tenant_id=uuid4(), term_id=uuid4(), class_id=uuid4(), by=by, now=NOW
    )
    assert (release.visible, release.version) == (True, 1)
    with pytest.raises(InvalidStateTransition):
        release.release_again(by=by, now=NOW)
    with pytest.raises(ValidationFailed):
        release.withdraw(by=by, reason="   ", now=NOW)
    release.withdraw(by=by, reason="  Exam   remarked ", now=NOW)
    assert (release.status, release.withdraw_reason, release.visible) == (
        ReleaseStatus.WITHDRAWN,
        "Exam remarked",
        False,
    )
    with pytest.raises(InvalidStateTransition):
        release.withdraw(by=by, reason="again", now=NOW)
    release.release_again(by=by, now=NOW)
    assert (release.version, release.visible, release.withdraw_reason) == (2, True, None)


def test_comment_text() -> None:
    assert comment_text("  Works   hard. ") == "Works hard."
    assert comment_text("   ") is None
    with pytest.raises(ValidationFailed):
        comment_text("x" * 501)
