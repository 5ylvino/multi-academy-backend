"""Safeguarding concerns and their case record.

Any member of staff who is worried about a child records a concern. Only the school's
safeguarding leads (``safeguarding:manage``) can see concerns; the person who reported one can see
what they wrote and its status, nothing more. Parents and students never see them.

A concern is never edited or deleted. Everything after it is added to its chronology: notes,
contact with parents, referrals to outside agencies, the decision to close and why. Free text is
encrypted at rest, and every time a lead opens a case it is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from app.domain.shared.entity import AggregateRoot
from app.domain.shared.errors import InvalidStateTransition, ValidationFailed
from app.domain.shared.events import DomainEvent
from app.domain.shared.ids import new_id


class Category(StrEnum):
    PHYSICAL = "physical"
    EMOTIONAL = "emotional"
    SEXUAL = "sexual"
    NEGLECT = "neglect"
    BULLYING = "bullying"
    ONLINE = "online"
    SELF_HARM = "self_harm"
    DOMESTIC = "domestic"
    ATTENDANCE = "attendance"
    OTHER = "other"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"  # the child may be at immediate risk: leads are alerted at once


class CaseStatus(StrEnum):
    OPEN = "open"
    MONITORING = "monitoring"
    REFERRED = "referred"
    CLOSED = "closed"


class EntryKind(StrEnum):
    NOTE = "note"
    PARENT_CONTACT = "parent_contact"
    REFERRAL = "referral"
    STATUS = "status"
    ASSIGNED = "assigned"


@dataclass(frozen=True, kw_only=True)
class UrgentConcernRaised(DomainEvent):
    name: ClassVar[str] = "safeguarding.urgent_concern"
    concern_id: UUID


@dataclass(frozen=True, slots=True)
class Entry:
    """One line of the chronology. ``text`` is plaintext in memory, encrypted in storage."""

    id: UUID
    concern_id: UUID
    kind: EntryKind
    text: str
    by: UUID
    at: datetime
    agency: str | None = None
    agency_reference: str | None = None
    status: CaseStatus | None = None


def _text(value: str, field_name: str, limit: int = 20_000) -> str:
    clean = value.strip()
    if not clean:
        raise ValidationFailed("Please write what happened.", details={"field": field_name})
    if len(clean) > limit:
        raise ValidationFailed("That is too long.", details={"field": field_name})
    return clean


@dataclass(kw_only=True, eq=False)
class Concern(AggregateRoot):
    tenant_id: UUID
    student_id: UUID
    category: Category
    severity: Severity
    summary: str
    observed_at: datetime
    reported_by: UUID
    reported_at: datetime
    status: CaseStatus = CaseStatus.OPEN
    lead_id: UUID | None = None
    closed_at: datetime | None = None
    new_entries: list[Entry] = field(default_factory=list)

    @classmethod
    def report(
        cls,
        *,
        tenant_id: UUID,
        student_id: UUID,
        category: Category,
        severity: Severity,
        account: str,
        action_taken: str | None,
        observed_at: datetime,
        by: UUID,
        now: datetime,
    ) -> Concern:
        if observed_at > now + timedelta(minutes=5):
            raise ValidationFailed(
                "When it happened can't be in the future.", details={"field": "observed_at"}
            )
        body = _text(account, "account")
        concern = cls(
            id=new_id(),
            tenant_id=tenant_id,
            student_id=student_id,
            category=category,
            severity=severity,
            summary=body,
            observed_at=observed_at,
            reported_by=by,
            reported_at=now,
        )
        if action_taken and action_taken.strip():
            concern._add(
                EntryKind.NOTE, f"Action taken at the time: {action_taken.strip()}", by, now
            )
        if severity is Severity.URGENT:
            concern.record(UrgentConcernRaised(tenant_id=tenant_id, concern_id=concern.id))
        return concern

    def add_note(
        self, *, text: str, by: UUID, now: datetime, parent_contact: bool = False
    ) -> Entry:
        self._open()
        kind = EntryKind.PARENT_CONTACT if parent_contact else EntryKind.NOTE
        return self._add(kind, _text(text, "text"), by, now)

    def refer(
        self, *, agency: str, reference: str | None, text: str, by: UUID, now: datetime
    ) -> Entry:
        self._open()
        name = " ".join(agency.split())
        if not 2 <= len(name) <= 120:
            raise ValidationFailed("Name the agency.", details={"field": "agency"})
        self.status = CaseStatus.REFERRED
        return self._add(
            EntryKind.REFERRAL,
            _text(text, "text"),
            by,
            now,
            agency=name,
            agency_reference=(reference or "").strip()[:80] or None,
            status=CaseStatus.REFERRED,
        )

    def assign(self, *, lead_id: UUID, by: UUID, now: datetime, lead_name: str) -> Entry:
        self._open()
        self.lead_id = lead_id
        return self._add(EntryKind.ASSIGNED, f"Case lead: {lead_name}", by, now)

    def set_status(self, *, status: CaseStatus, reason: str, by: UUID, now: datetime) -> Entry:
        if status is self.status:
            raise ValidationFailed("The case already has that status.", details={"field": "status"})
        if self.status is CaseStatus.CLOSED and status is not CaseStatus.OPEN:
            raise InvalidStateTransition("Reopen the case first.", code="case_closed")
        self.status = status
        self.closed_at = now if status is CaseStatus.CLOSED else None
        return self._add(EntryKind.STATUS, _text(reason, "reason"), by, now, status=status)

    def _open(self) -> None:
        if self.status is CaseStatus.CLOSED:
            raise InvalidStateTransition("Reopen the case first.", code="case_closed")

    def _add(
        self,
        kind: EntryKind,
        text: str,
        by: UUID,
        now: datetime,
        *,
        agency: str | None = None,
        agency_reference: str | None = None,
        status: CaseStatus | None = None,
    ) -> Entry:
        entry = Entry(
            id=new_id(),
            concern_id=self.id,
            kind=kind,
            text=text,
            by=by,
            at=now,
            agency=agency,
            agency_reference=agency_reference,
            status=status,
        )
        self.new_entries.append(entry)
        return entry
