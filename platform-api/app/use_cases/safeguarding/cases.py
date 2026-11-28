"""Safeguarding: report a concern, and the leads' case work.

- Any member of staff with ``safeguarding:report`` can record a concern about a student and later
  see what they reported and its status, nothing more.
- Safeguarding leads (``safeguarding:manage``, by default the director, principal and head
  teacher; schools can grant it to their designated lead) see every case, add to the chronology,
  record contact with parents and referrals, assign a case lead and close or reopen cases.
- Leads are told at once in their inbox. Urgent concerns are also emailed to them.
- Free text is encrypted before it reaches the database. Opening a case is recorded each time;
  the activity log never holds the text itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.domain.communication.inbox import Notification, NotificationKind
from app.domain.identity.permissions import PermissionSet
from app.domain.identity.roles import Realm, SchoolRole
from app.domain.identity.school_catalog import SCHOOL_ROLE_PERMISSIONS
from app.domain.identity.user import UserAccount
from app.domain.safeguarding.concerns import (
    CaseStatus,
    Category,
    Concern,
    Entry,
    Severity,
)
from app.domain.shared.errors import NotFound, ValidationFailed
from app.domain.shared.value_objects import Page
from app.use_cases.shared import audit
from app.use_cases.shared.pagination import clamp_limit
from app.use_cases.shared.ports import Clock, Scope, SecretBox, UnitOfWork, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal

MANAGE = "safeguarding:manage"
LEAD_ROLES = frozenset(
    role.value for role, grants in SCHOOL_ROLE_PERMISSIONS.items() if MANAGE in grants
)


@dataclass(frozen=True, slots=True)
class ConcernSummary:
    id: UUID
    student_id: UUID
    student_name: str
    category: str
    severity: str
    status: str
    observed_at: datetime
    reported_by: UUID
    reported_by_name: str
    reported_at: datetime
    lead_id: UUID | None
    lead_name: str | None
    closed_at: datetime | None


@dataclass(frozen=True, slots=True)
class EntryView:
    id: UUID
    kind: str
    text: str
    by: UUID
    by_name: str
    at: datetime
    agency: str | None
    agency_reference: str | None
    status: str | None


@dataclass(frozen=True, slots=True)
class CaseView:
    concern: ConcernSummary
    account: str
    chronology: list[EntryView]


@dataclass(frozen=True, slots=True)
class MyReport:
    concern: ConcernSummary
    account: str


@dataclass(frozen=True, slots=True)
class ReportCommand:
    student_id: UUID
    category: str
    severity: str
    account: str
    observed_at: datetime
    action_taken: str | None = None


class Safeguarding:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock, box: SecretBox) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._box = box

    # ---- staff ------------------------------------------------------------------------------

    async def report(self, actor: Principal, cmd: ReportCommand) -> MyReport:
        actor.require_realm(Realm.SCHOOL)
        actor.require("safeguarding:report")
        try:
            category, severity = Category(cmd.category), Severity(cmd.severity)
        except ValueError as exc:
            raise ValidationFailed("Pick a category and a level of concern.") from exc
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            student = await uow.users.get(cmd.student_id)
            if (
                student is None
                or student.tenant_id != tenant_id
                or SchoolRole.STUDENT.value not in student.roles
            ):
                raise NotFound("Student not found.")
            concern = Concern.report(
                tenant_id=tenant_id,
                student_id=student.id,
                category=category,
                severity=severity,
                account=cmd.account,
                action_taken=cmd.action_taken,
                observed_at=cmd.observed_at,
                by=actor.user_id,
                now=now,
            )
            await uow.safeguarding.add(concern, summary_ciphertext=self._seal(concern.summary))
            await self._save(uow, concern)
            uow.track(concern)
            leads = await _leads(uow, tenant_id)
            await uow.communication.notify(
                [
                    Notification.new(
                        tenant_id=tenant_id,
                        user_id=lead.id,
                        kind=NotificationKind.GENERAL,
                        title=(
                            "URGENT safeguarding concern"
                            if severity is Severity.URGENT
                            else "New safeguarding concern"
                        ),
                        body="A member of staff has recorded a concern about a student.",
                        link_type="safeguarding_concern",
                        link_id=concern.id,
                        now=now,
                    )
                    for lead in leads
                    if lead.id != actor.user_id
                ]
            )
            uow.audit(
                audit.by(
                    actor,
                    "safeguarding.concern_reported",
                    target_type="safeguarding_concern",
                    target_id=concern.id,
                    student_id=str(student.id),
                    category=category.value,
                    severity=severity.value,
                )
            )
            names = await uow.users.school_names({student.id, actor.user_id})
            await uow.commit()
        return MyReport(concern=_summary(concern, names), account=concern.summary)

    async def my_reports(
        self, actor: Principal, *, cursor: str | None, limit: int | None
    ) -> Page[MyReport]:
        actor.require_realm(Realm.SCHOOL)
        actor.require("safeguarding:report")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            page = await uow.safeguarding.search(
                status=None,
                student_id=None,
                reported_by=actor.user_id,
                cursor=cursor,
                limit=clamp_limit(limit),
            )
            names = await _names(uow, page.items)
            reports = []
            for c in page.items:
                loaded = await uow.safeguarding.get(c.id)
                assert loaded is not None
                reports.append(MyReport(concern=_summary(c, names), account=self._open(loaded[1])))
        return Page(items=reports, next_cursor=page.next_cursor)

    # ---- leads ------------------------------------------------------------------------------

    async def cases(
        self,
        actor: Principal,
        *,
        status: str | None,
        student_id: UUID | None,
        cursor: str | None,
        limit: int | None,
    ) -> Page[ConcernSummary]:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        if status is not None and status not in {s.value for s in CaseStatus}:
            raise ValidationFailed("Unknown status.", details={"field": "status"})
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            page = await uow.safeguarding.search(
                status=status,
                student_id=student_id,
                reported_by=None,
                cursor=cursor,
                limit=clamp_limit(limit),
            )
            names = await _names(uow, page.items)
        return Page(items=[_summary(c, names) for c in page.items], next_cursor=page.next_cursor)

    async def counts(self, actor: Principal) -> dict[str, int]:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            return await uow.safeguarding.counts()

    async def open(self, actor: Principal, concern_id: UUID) -> CaseView:
        """The full case. Every opening is recorded in the activity log."""
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            concern, sealed = await self._get(uow, concern_id)
            uow.audit(
                audit.by(
                    actor,
                    "safeguarding.case_opened",
                    target_type="safeguarding_concern",
                    target_id=concern.id,
                    student_id=str(concern.student_id),
                )
            )
            view = await self._case(uow, concern, sealed)
            await uow.commit()
        return view

    async def add_note(
        self, actor: Principal, concern_id: UUID, *, text: str, parent_contact: bool
    ) -> CaseView:
        def change(concern: Concern, now: datetime) -> Entry:
            return concern.add_note(
                text=text, by=actor.user_id, now=now, parent_contact=parent_contact
            )

        action = (
            "safeguarding.parent_contact_recorded" if parent_contact else "safeguarding.note_added"
        )
        return await self._change(actor, concern_id, action, change)

    async def refer(
        self,
        actor: Principal,
        concern_id: UUID,
        *,
        agency: str,
        reference: str | None,
        text: str,
    ) -> CaseView:
        def change(concern: Concern, now: datetime) -> Entry:
            return concern.refer(
                agency=agency, reference=reference, text=text, by=actor.user_id, now=now
            )

        return await self._change(actor, concern_id, "safeguarding.referred", change, agency=agency)

    async def assign(self, actor: Principal, concern_id: UUID, *, lead_id: UUID) -> CaseView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            lead = await uow.users.get(lead_id)
        if lead is None or lead.tenant_id != actor.school_tenant_id or not _is_lead(lead):
            raise ValidationFailed(
                "The case lead must be a safeguarding lead.", details={"field": "lead_id"}
            )

        def change(concern: Concern, now: datetime) -> Entry:
            return concern.assign(
                lead_id=lead.id, by=actor.user_id, now=now, lead_name=lead.full_name
            )

        return await self._change(
            actor, concern_id, "safeguarding.lead_assigned", change, lead_id=str(lead.id)
        )

    async def set_status(
        self, actor: Principal, concern_id: UUID, *, status: str, reason: str
    ) -> CaseView:
        try:
            target = CaseStatus(status)
        except ValueError as exc:
            raise ValidationFailed("Unknown status.", details={"field": "status"}) from exc

        def change(concern: Concern, now: datetime) -> Entry:
            return concern.set_status(status=target, reason=reason, by=actor.user_id, now=now)

        return await self._change(
            actor, concern_id, "safeguarding.status_changed", change, status=target.value
        )

    # ---- helpers ----------------------------------------------------------------------------

    async def _change(
        self,
        actor: Principal,
        concern_id: UUID,
        action: str,
        change: Callable[[Concern, datetime], Entry],
        **meta: Any,
    ) -> CaseView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            concern, sealed = await self._get(uow, concern_id, for_update=True)
            entry = change(concern, now)
            await self._save(uow, concern)
            uow.audit(
                audit.by(
                    actor,
                    action,
                    target_type="safeguarding_concern",
                    target_id=concern.id,
                    entry_id=str(entry.id),
                    **meta,
                )
            )
            view = await self._case(uow, concern, sealed)
            await uow.commit()
        return view

    async def _save(self, uow: UnitOfWork, concern: Concern) -> None:
        await uow.safeguarding.save(
            concern, entry_ciphertexts={e.id: self._seal(e.text) for e in concern.new_entries}
        )

    async def _get(
        self, uow: UnitOfWork, concern_id: UUID, *, for_update: bool = False
    ) -> tuple[Concern, str]:
        found = await uow.safeguarding.get(concern_id, for_update=for_update)
        if found is None:
            raise NotFound("Concern not found.")
        return found

    async def _case(self, uow: UnitOfWork, concern: Concern, sealed: str) -> CaseView:
        entries = await uow.safeguarding.entries(concern.id)
        people = {e.by for e, _ in entries}
        names = await _names(uow, [concern], extra=people)
        return CaseView(
            concern=_summary(concern, names),
            account=self._open(sealed),
            chronology=[
                EntryView(
                    id=e.id,
                    kind=e.kind.value,
                    text=self._open(text),
                    by=e.by,
                    by_name=names.get(e.by, ""),
                    at=e.at,
                    agency=e.agency,
                    agency_reference=e.agency_reference,
                    status=e.status.value if e.status else None,
                )
                for e, text in entries
            ],
        )

    def _seal(self, text: str) -> str:
        return self._box.encrypt(text.encode())

    def _open(self, sealed: str) -> str:
        return self._box.decrypt(sealed).decode()


def _is_lead(user: UserAccount) -> bool:
    return PermissionSet.for_realm(
        Realm.SCHOOL, user.roles, user.capabilities, primary_owner=user.primary_owner
    ).allows(MANAGE)


async def _leads(uow: UnitOfWork, tenant_id: UUID) -> list[UserAccount]:
    found = await uow.users.school_users_with_access(tenant_id, roles=LEAD_ROLES, capability=MANAGE)
    return [u for u in found if _is_lead(u)]


async def leads_of(uow: UnitOfWork, tenant_id: UUID) -> list[UserAccount]:
    """The school's safeguarding leads (for the urgent email)."""
    return await _leads(uow, tenant_id)


async def _names(
    uow: UnitOfWork, concerns: list[Concern], *, extra: set[UUID] | None = None
) -> dict[UUID, str]:
    people: set[UUID] = set(extra or set())
    for c in concerns:
        people |= {c.student_id, c.reported_by}
        if c.lead_id:
            people.add(c.lead_id)
    return await uow.users.school_names(people)


def _summary(c: Concern, names: dict[UUID, str]) -> ConcernSummary:
    return ConcernSummary(
        id=c.id,
        student_id=c.student_id,
        student_name=names.get(c.student_id, ""),
        category=c.category.value,
        severity=c.severity.value,
        status=c.status.value,
        observed_at=c.observed_at,
        reported_by=c.reported_by,
        reported_by_name=names.get(c.reported_by, ""),
        reported_at=c.reported_at,
        lead_id=c.lead_id,
        lead_name=names.get(c.lead_id) if c.lead_id else None,
        closed_at=c.closed_at,
    )
