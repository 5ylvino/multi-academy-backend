from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.domain.safeguarding.concerns import (
    CaseStatus,
    Category,
    Concern,
    EntryKind,
    Severity,
    UrgentConcernRaised,
)
from app.domain.shared.errors import InvalidStateTransition, ValidationFailed

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)


def _concern(severity: Severity = Severity.MEDIUM, action: str | None = None) -> Concern:
    return Concern.report(
        tenant_id=uuid4(),
        student_id=uuid4(),
        category=Category.BULLYING,
        severity=severity,
        account="  Pushed in the corridor.  ",
        action_taken=action,
        observed_at=NOW - timedelta(hours=2),
        by=uuid4(),
        now=NOW,
    )


def test_report_records_account_and_action() -> None:
    c = _concern(action="Separated them")
    assert c.summary == "Pushed in the corridor."
    assert c.status is CaseStatus.OPEN
    assert [e.kind for e in c.new_entries] == [EntryKind.NOTE]
    assert c.pull_events() == []


def test_urgent_raises_alert() -> None:
    (event,) = _concern(Severity.URGENT).pull_events()
    assert isinstance(event, UrgentConcernRaised)


def test_report_validation() -> None:
    with pytest.raises(ValidationFailed):
        Concern.report(
            tenant_id=uuid4(),
            student_id=uuid4(),
            category=Category.OTHER,
            severity=Severity.LOW,
            account="   ",
            action_taken=None,
            observed_at=NOW,
            by=uuid4(),
            now=NOW,
        )
    with pytest.raises(ValidationFailed):
        Concern.report(
            tenant_id=uuid4(),
            student_id=uuid4(),
            category=Category.OTHER,
            severity=Severity.LOW,
            account="x",
            action_taken=None,
            observed_at=NOW + timedelta(hours=1),
            by=uuid4(),
            now=NOW,
        )


def test_case_lifecycle() -> None:
    c = _concern()
    lead = uuid4()
    c.add_note(text="Spoke to her", by=lead, now=NOW, parent_contact=True)
    entry = c.refer(agency=" Police ", reference=" REF-1 ", text="Reported", by=lead, now=NOW)
    assert (entry.agency, entry.agency_reference) == ("Police", "REF-1")
    assert c.status is CaseStatus.REFERRED
    with pytest.raises(ValidationFailed):
        c.refer(agency="x", reference=None, text="t", by=lead, now=NOW)
    c.assign(lead_id=lead, by=lead, now=NOW, lead_name="Mrs Ade")
    assert c.lead_id == lead
    c.set_status(status=CaseStatus.CLOSED, reason="Resolved", by=lead, now=NOW)
    assert c.closed_at == NOW
    with pytest.raises(InvalidStateTransition):
        c.add_note(text="late", by=lead, now=NOW)
    with pytest.raises(InvalidStateTransition):
        c.set_status(status=CaseStatus.MONITORING, reason="x", by=lead, now=NOW)
    with pytest.raises(ValidationFailed):
        c.set_status(status=CaseStatus.CLOSED, reason="x", by=lead, now=NOW)
    c.set_status(status=CaseStatus.OPEN, reason="New disclosure", by=lead, now=NOW)
    assert c.closed_at is None
    assert [e.kind for e in c.new_entries] == [
        EntryKind.PARENT_CONTACT,
        EntryKind.REFERRAL,
        EntryKind.ASSIGNED,
        EntryKind.STATUS,
        EntryKind.STATUS,
    ]
