from __future__ import annotations

from datetime import UTC, date, datetime, time
from uuid import uuid4

import pytest

from app.domain.attendance.register import (
    AttendanceStatus,
    AttendanceSummary,
    StudentMark,
    ensure_markable_day,
    parse_status,
)
from app.domain.attendance.staff import (
    AttendanceSettings,
    CheckInStatus,
    Site,
    StaffDayStatus,
)
from app.domain.shared.errors import ValidationFailed

LAGOS_CAMPUS = Site(name="Main campus", latitude=6.5244, longitude=3.3792, radius_m=150)


def _mark() -> StudentMark:
    return StudentMark.record(
        tenant_id=uuid4(),
        class_id=uuid4(),
        session_id=uuid4(),
        student_id=uuid4(),
        day=date(2026, 11, 2),
        status=AttendanceStatus.ABSENT,
        marked_by=uuid4(),
        now=datetime.now(UTC),
        note="  ",
    )


def test_correction_needs_reason_and_a_change() -> None:
    mark = _mark()
    assert mark.note is None
    with pytest.raises(ValidationFailed):
        mark.correct(status=AttendanceStatus.PRESENT, reason=" ", by=uuid4(), now=datetime.now(UTC))
    with pytest.raises(ValidationFailed):
        mark.correct(
            status=AttendanceStatus.ABSENT, reason="typo", by=uuid4(), now=datetime.now(UTC)
        )
    previous = mark.correct(
        status=AttendanceStatus.EXCUSED,
        reason="  doctor's   note ",
        by=uuid4(),
        now=datetime.now(UTC),
    )
    assert previous is AttendanceStatus.ABSENT
    assert mark.correction_reason == "doctor's note"


def test_markable_day_rules() -> None:
    term = {"term_start": date(2026, 9, 7), "term_end": date(2026, 12, 18)}
    ensure_markable_day(date(2026, 11, 2), today=date(2026, 11, 2), **term)
    with pytest.raises(ValidationFailed, match="future"):
        ensure_markable_day(date(2026, 11, 3), today=date(2026, 11, 2), **term)
    with pytest.raises(ValidationFailed, match="outside"):
        ensure_markable_day(date(2026, 9, 1), today=date(2026, 11, 2), **term)
    with pytest.raises(ValidationFailed):
        parse_status("sick")


def test_summary_rate() -> None:
    assert AttendanceSummary(0, 0, 0, 0).attendance_rate is None
    assert AttendanceSummary(present=7, late=1, absent=1, excused=1).attendance_rate == 0.8


def test_site_distance_and_bounds() -> None:
    assert LAGOS_CAMPUS.contains(6.5250, 3.3795)  # about 70 m away
    assert not LAGOS_CAMPUS.contains(6.5300, 3.3792)  # about 620 m away
    with pytest.raises(ValidationFailed):
        Site(name="x", latitude=91, longitude=0, radius_m=100)
    with pytest.raises(ValidationFailed):
        Site(name="x", latitude=0, longitude=0, radius_m=10)


def _settings(**kw: object) -> AttendanceSettings:
    s = AttendanceSettings.defaults(uuid4(), check_in_roles=frozenset({"class_teacher"}))
    s.update(
        window_opens=time(6, 30),
        late_after=time(8, 0),
        window_closes=time(10, 0),
        business_days=frozenset({1, 2, 3, 4, 5}),
        check_in_roles=frozenset({"class_teacher"}),
        sites=(LAGOS_CAMPUS,),
        require_location=bool(kw.get("require_location", False)),
    )
    return s


def test_settings_validation() -> None:
    s = _settings()
    with pytest.raises(ValidationFailed):
        s.update(
            window_opens=time(9),
            late_after=time(8),
            window_closes=time(10),
            business_days=frozenset({1}),
            check_in_roles=frozenset(),
            sites=(),
            require_location=False,
        )
    with pytest.raises(ValidationFailed, match="site"):
        s.update(
            window_opens=time(6),
            late_after=time(8),
            window_closes=time(10),
            business_days=frozenset({1}),
            check_in_roles=frozenset(),
            sites=(),
            require_location=True,
        )


def test_check_in_window_and_lateness() -> None:
    s = _settings()
    monday = date(2026, 11, 2)
    assert s.check_in_status(datetime.combine(monday, time(7, 45))) is CheckInStatus.PRESENT
    assert s.check_in_status(datetime.combine(monday, time(8, 30))) is CheckInStatus.LATE
    with pytest.raises(ValidationFailed, match="open from"):
        s.check_in_status(datetime.combine(monday, time(11, 0)))
    with pytest.raises(ValidationFailed, match="school days"):
        s.check_in_status(datetime.combine(date(2026, 11, 1), time(7, 0)))  # Sunday


def test_day_status() -> None:
    s = _settings()
    monday = date(2026, 11, 2)
    during = datetime.combine(monday, time(9))
    after = datetime.combine(monday, time(11))
    assert s.day_status(day=monday, local_now=during, checked_in=None) is StaffDayStatus.PENDING
    assert s.day_status(day=monday, local_now=after, checked_in=None) is StaffDayStatus.ABSENT
    assert (
        s.day_status(day=monday, local_now=after, checked_in=CheckInStatus.LATE)
        is StaffDayStatus.LATE
    )
    assert (
        s.day_status(day=date(2026, 11, 1), local_now=after, checked_in=None)
        is StaffDayStatus.NOT_REQUIRED
    )


def test_location_matching() -> None:
    optional = _settings()
    assert optional.match_site(None, None) is None
    assert optional.match_site(6.5250, 3.3795) == "Main campus"
    required = _settings(require_location=True)
    with pytest.raises(ValidationFailed) as missing:
        required.match_site(None, None)
    assert missing.value.code == "location_required"
    with pytest.raises(ValidationFailed) as away:
        required.match_site(6.60, 3.30)
    assert away.value.code == "outside_site"
