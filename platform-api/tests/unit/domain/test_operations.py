from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.operations.clinic import ClinicVisit, ClinicVisitClosed, HealthRecord, Outcome
from app.domain.operations.gate import Collector, Release, ReleaseStatus, Visit
from app.domain.operations.hostel import Exeat, ExeatStatus, Hostel, HostelKind, Room
from app.domain.operations.transport import Boarding, BoardingKind, Direction, Route, Stop
from app.domain.shared.errors import InvalidStateTransition, ValidationFailed

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
TODAY = date(2026, 11, 27)
TENANT, STUDENT, BY = uuid4(), uuid4(), uuid4()


def _record(**over: object) -> HealthRecord:
    args: dict[str, object] = {
        "tenant_id": TENANT,
        "student_id": STUDENT,
        "blood_group": " o+ ",
        "genotype": "as",
        "allergies": ["Peanuts", " peanuts ", "", "Dust"],
        "conditions": [],
        "medications": [],
        "notes": "  ",
        "staff_alert": "Carries an EpiPen",
        "doctor": None,
        "by": BY,
        "now": NOW,
    }
    args.update(over)
    return HealthRecord.build(**args)  # type: ignore[arg-type]


def test_health_record_normalises_input() -> None:
    record = _record()
    assert record.blood_group == "O+"
    assert record.genotype == "AS"
    assert record.allergies == ("Peanuts", "Dust")
    assert record.notes is None
    assert record.student_id == STUDENT


@pytest.mark.parametrize(
    "over",
    [
        {"blood_group": "C+"},
        {"genotype": "AB"},
        {"allergies": [f"a{i}" for i in range(31)]},
        {"staff_alert": "x" * 301},
    ],
)
def test_health_record_rejects_bad_values(over: dict[str, object]) -> None:
    with pytest.raises(ValidationFailed):
        _record(**over)


def _visit() -> ClinicVisit:
    return ClinicVisit.open(
        tenant_id=TENANT, student_id=STUDENT, complaint="Fever", arrived_at=None, by=BY, now=NOW
    )


def test_visit_arrival_must_be_recent() -> None:
    for when in (NOW + timedelta(hours=1), NOW - timedelta(days=8)):
        with pytest.raises(ValidationFailed):
            ClinicVisit.open(
                tenant_id=TENANT,
                student_id=STUDENT,
                complaint="Fever",
                arrived_at=when,
                by=BY,
                now=NOW,
            )
    with pytest.raises(ValidationFailed):
        ClinicVisit.open(
            tenant_id=TENANT, student_id=STUDENT, complaint=" ", arrived_at=None, by=BY, now=NOW
        )


def test_visit_update_and_close() -> None:
    visit = _visit()
    with pytest.raises(ValidationFailed):
        visit.update(assessment=None, treatment=None, temperature_dc=299, medication_given=[])
    visit.update(
        assessment="Flu", treatment="Paracetamol", temperature_dc=385, medication_given=["PCM"]
    )
    visit.close(outcome=Outcome.SENT_HOME, now=NOW)
    assert not visit.is_open
    [event] = visit.pull_events()
    assert isinstance(event, ClinicVisitClosed)
    assert event.urgent is True
    with pytest.raises(InvalidStateTransition):
        visit.update(assessment="x", treatment=None, temperature_dc=None, medication_given=[])
    with pytest.raises(InvalidStateTransition):
        visit.close(outcome=Outcome.RETURNED_TO_CLASS, now=NOW)


@pytest.mark.parametrize(
    ("outcome", "urgent"),
    [
        (Outcome.RETURNED_TO_CLASS, False),
        (Outcome.RESTED_IN_CLINIC, False),
        (Outcome.COLLECTED_BY_PARENT, False),
        (Outcome.SENT_HOME, True),
        (Outcome.REFERRED_TO_HOSPITAL, True),
    ],
)
def test_only_outcomes_parents_must_act_on_are_urgent(outcome: Outcome, urgent: bool) -> None:
    visit = _visit()
    visit.close(outcome=outcome, now=NOW)
    [event] = visit.pull_events()
    assert isinstance(event, ClinicVisitClosed)
    assert event.urgent is urgent


def _collector(valid_on: date | None = None, student_id: object = STUDENT) -> Collector:
    return Collector.add(
        tenant_id=TENANT,
        student_id=student_id,  # type: ignore[arg-type]
        name="Aunty Ifeoma",
        relationship="aunt",
        phone="+234 803 000 0001",
        id_document=None,
        valid_on=valid_on,
        today=TODAY,
        by=BY,
        now=NOW,
    )


def test_collector_validity() -> None:
    every_day = _collector()
    assert every_day.allowed_on(TODAY + timedelta(days=30))
    one_day = _collector(valid_on=TODAY)
    assert one_day.allowed_on(TODAY)
    assert not one_day.allowed_on(TODAY + timedelta(days=1))
    every_day.active = False
    assert not every_day.allowed_on(TODAY)
    with pytest.raises(ValidationFailed):
        _collector(valid_on=TODAY - timedelta(days=1))


def _decide(**over: object) -> Release:
    args: dict[str, object] = {
        "tenant_id": TENANT,
        "student_id": STUDENT,
        "guardian_id": None,
        "collector": None,
        "refused_name": None,
        "note": None,
        "today": TODAY,
        "by": BY,
        "now": NOW,
    }
    args.update(over)
    return Release.decide(**args)  # type: ignore[arg-type]


def test_release_needs_exactly_one_choice() -> None:
    with pytest.raises(ValidationFailed):
        _decide()
    with pytest.raises(ValidationFailed):
        _decide(guardian_id=uuid4(), collector=_collector())


def test_release_to_a_listed_collector_only() -> None:
    out = _decide(collector=_collector())
    assert out.status is ReleaseStatus.RELEASED
    assert out.collector_name == "Aunty Ifeoma"
    with pytest.raises(ValidationFailed):
        _decide(collector=_collector(student_id=uuid4()))
    tomorrow = _collector(valid_on=TODAY + timedelta(days=1))
    with pytest.raises(ValidationFailed):
        _decide(collector=tomorrow)


def test_refusal_needs_a_name_and_a_note() -> None:
    with pytest.raises(ValidationFailed):
        _decide(refused_name="Emeka Stranger")
    refused = _decide(refused_name="Emeka Stranger", note="No ID")
    assert refused.status is ReleaseStatus.REFUSED
    assert refused.collector_name == "Emeka Stranger"


def test_visitor_signs_out_once() -> None:
    visit = Visit.sign_in(
        tenant_id=TENANT,
        name="Mr Bello",
        phone=None,
        organisation=None,
        purpose="Delivery",
        host_id=None,
        badge=None,
        vehicle=None,
        by=BY,
        now=NOW,
    )
    visit.sign_out(by=BY, now=NOW)
    with pytest.raises(InvalidStateTransition):
        visit.sign_out(by=BY, now=NOW)
    with pytest.raises(ValidationFailed):
        Visit.sign_in(
            tenant_id=TENANT,
            name="X",
            phone=None,
            organisation=None,
            purpose="Delivery",
            host_id=None,
            badge=None,
            vehicle=None,
            by=BY,
            now=NOW,
        )


def _route(capacity: int = 30) -> Route:
    return Route.create(
        tenant_id=TENANT,
        name=" Lekki  run ",
        code="r-1",
        vehicle_plate="lnd 123 ab",
        capacity=capacity,
        driver_id=None,
        now=NOW,
    )


def test_route_normalises_and_validates() -> None:
    route = _route()
    assert (route.name, route.code, route.vehicle_plate) == ("Lekki run", "R-1", "LND 123 AB")
    for over in ({"capacity": 0}, {"code": "has space"}, {"vehicle_plate": "!!"}):
        args: dict[str, object] = {
            "tenant_id": TENANT,
            "name": "Lekki",
            "code": "R1",
            "vehicle_plate": None,
            "capacity": 10,
            "driver_id": None,
            "now": NOW,
        }
        args.update(over)
        with pytest.raises(ValidationFailed):
            Route.create(**args)  # type: ignore[arg-type]


def test_route_change_guards_riders() -> None:
    route = _route()
    keep = {"name": "Lekki", "vehicle_plate": None, "driver_id": None}
    with pytest.raises(ValidationFailed):
        route.change(**keep, capacity=5, active=True, riders=6)  # type: ignore[arg-type]
    with pytest.raises(InvalidStateTransition):
        route.change(**keep, capacity=30, active=False, riders=1)  # type: ignore[arg-type]
    route.change(**keep, capacity=6, active=False, riders=0)  # type: ignore[arg-type]
    assert (route.capacity, route.active) == (6, False)


def test_stop_position_range() -> None:
    with pytest.raises(ValidationFailed):
        Stop.add(
            tenant_id=TENANT,
            route_id=uuid4(),
            name="Gate",
            position=41,
            morning_at=None,
            afternoon_at=None,
            landmark=None,
        )


@pytest.mark.parametrize(
    ("previous", "kind", "ok"),
    [
        (None, BoardingKind.BOARDED, True),
        (None, BoardingKind.ABSENT, True),
        (None, BoardingKind.DROPPED, False),
        (BoardingKind.ABSENT, BoardingKind.BOARDED, True),
        (BoardingKind.ABSENT, BoardingKind.ABSENT, False),
        (BoardingKind.BOARDED, BoardingKind.BOARDED, False),
        (BoardingKind.BOARDED, BoardingKind.DROPPED, True),
        (BoardingKind.DROPPED, BoardingKind.BOARDED, False),
    ],
)
def test_boarding_order(previous: BoardingKind | None, kind: BoardingKind, ok: bool) -> None:
    def record() -> Boarding:
        return Boarding.record(
            tenant_id=TENANT,
            student_id=STUDENT,
            route_id=uuid4(),
            stop_id=None,
            direction=Direction.TO_SCHOOL,
            kind=kind,
            previous=previous,
            day=TODAY,
            by=BY,
            now=NOW,
        )

    if ok:
        assert record().kind is kind
    else:
        with pytest.raises(InvalidStateTransition):
            record()


def _exeat(**over: object) -> Exeat:
    args: dict[str, object] = {
        "tenant_id": TENANT,
        "student_id": STUDENT,
        "reason": "Wedding",
        "collector_name": "Ngozi  Obi",
        "leave_at": NOW + timedelta(hours=2),
        "return_by": NOW + timedelta(days=2),
        "by": BY,
        "now": NOW,
    }
    args.update(over)
    return Exeat.request(**args)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "over",
    [
        {"leave_at": NOW - timedelta(hours=2)},
        {"return_by": NOW + timedelta(hours=1)},
        {"return_by": NOW + timedelta(days=40)},
        {"collector_name": "X"},
        {"reason": " "},
    ],
)
def test_exeat_request_rules(over: dict[str, object]) -> None:
    with pytest.raises(ValidationFailed):
        _exeat(**over)


def test_exeat_lifecycle() -> None:
    exeat = _exeat()
    assert exeat.collector_name == "Ngozi Obi"
    with pytest.raises(InvalidStateTransition):
        exeat.sign_out(now=NOW)
    with pytest.raises(InvalidStateTransition):
        exeat.approve(by=BY, note=None, now=NOW)  # the requester can't decide
    with pytest.raises(ValidationFailed):
        exeat.reject(by=uuid4(), note=" ", now=NOW)
    exeat.approve(by=uuid4(), note=None, now=NOW)
    with pytest.raises(InvalidStateTransition):
        exeat.approve(by=uuid4(), note=None, now=NOW)
    exeat.sign_out(now=NOW + timedelta(hours=2))
    with pytest.raises(InvalidStateTransition):
        exeat.cancel(now=NOW)
    assert not exeat.overdue(NOW + timedelta(days=1))
    assert exeat.overdue(NOW + timedelta(days=3))
    exeat.sign_in(now=NOW + timedelta(days=3))
    assert exeat.status is ExeatStatus.RETURNED
    assert exeat.returned_late
    with pytest.raises(InvalidStateTransition):
        exeat.sign_in(now=NOW)


def test_expired_exeat_cannot_start() -> None:
    exeat = _exeat()
    exeat.approve(by=uuid4(), note=None, now=NOW)
    with pytest.raises(InvalidStateTransition):
        exeat.sign_out(now=NOW + timedelta(days=3))


def test_rooms_and_hostels_guard_occupants() -> None:
    room = Room.add(tenant_id=TENANT, hostel_id=uuid4(), name="Room 1", capacity=4)
    with pytest.raises(ValidationFailed):
        room.change(name="Room 1", capacity=2, active=True, occupants=3)
    with pytest.raises(InvalidStateTransition):
        room.change(name="Room 1", capacity=4, active=False, occupants=1)
    with pytest.raises(ValidationFailed):
        Room.add(tenant_id=TENANT, hostel_id=uuid4(), name="Big", capacity=41)
    hostel = Hostel.create(
        tenant_id=TENANT, name="Amina", kind=HostelKind.GIRLS, warden_id=None, now=NOW
    )
    with pytest.raises(InvalidStateTransition):
        hostel.change(name="Amina", warden_id=None, active=False, residents=1)
    hostel.change(name="Amina House", warden_id=None, active=False, residents=0)
    assert not hostel.active
