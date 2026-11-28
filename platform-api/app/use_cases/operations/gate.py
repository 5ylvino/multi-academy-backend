"""The gate: who may collect each student, releases at home time, and visitors.

- Guardians, and staff with ``students:update``, keep a student's authorised collector list,
  including one-off collectors for a single day.
- Gate staff (``gate:manage``) see who may collect a student, release the student to one of
  them, or record a refusal. Guardians are told in the app either way.
- Gate staff sign visitors in and out; leaders (``gate:read``) see the logs and who is on site.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from app.domain.academics.sections import SectionFeature
from app.domain.communication.inbox import Notification, NotificationKind
from app.domain.identity.roles import Realm, SchoolRole
from app.domain.operations.gate import Collector, Release, ReleaseStatus, Visit
from app.domain.shared.errors import NotFound, PermissionDenied, ValidationFailed
from app.domain.shared.value_objects import Page
from app.use_cases.academics.sections import (
    ensure_student_in_scope,
    serve_student,
    visible_students,
)
from app.use_cases.shared import audit
from app.use_cases.shared.pagination import clamp_limit
from app.use_cases.shared.ports import Clock, SchoolTime, Scope, UnitOfWork, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal

_STUDENT = SchoolRole.STUDENT.value


@dataclass(frozen=True, slots=True)
class CollectorView:
    id: UUID
    name: str
    relationship: str
    phone: str | None
    id_document: str | None
    valid_on: date | None
    active: bool
    added_at: datetime

    @classmethod
    def of(cls, c: Collector) -> CollectorView:
        return cls(
            id=c.id,
            name=c.name,
            relationship=c.relationship,
            phone=c.phone,
            id_document=c.id_document,
            valid_on=c.valid_on,
            active=c.active,
            added_at=c.added_at,
        )


@dataclass(frozen=True, slots=True)
class GuardianOption:
    id: UUID
    name: str
    relationship: str


@dataclass(frozen=True, slots=True)
class PickupList:
    """Everyone who may collect the student today."""

    student_id: UUID
    student_name: str
    guardians: list[GuardianOption]
    collectors: list[CollectorView]


@dataclass(frozen=True, slots=True)
class ReleaseView:
    id: UUID
    student_id: UUID
    student_name: str
    status: str
    guardian_id: UUID | None
    collector_id: UUID | None
    collector_name: str
    note: str | None
    by: UUID
    at: datetime


@dataclass(frozen=True, slots=True)
class VisitView:
    id: UUID
    name: str
    phone: str | None
    organisation: str | None
    purpose: str
    host_id: UUID | None
    host_name: str | None
    badge: str | None
    vehicle: str | None
    signed_in_at: datetime
    signed_out_at: datetime | None


@dataclass(frozen=True, slots=True)
class CollectorCommand:
    name: str
    relationship: str
    phone: str | None = None
    id_document: str | None = None
    valid_on: date | None = None


@dataclass(frozen=True, slots=True)
class ReleaseCommand:
    student_id: UUID
    guardian_id: UUID | None = None
    collector_id: UUID | None = None
    refused_name: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class VisitorCommand:
    name: str
    purpose: str
    phone: str | None = None
    organisation: str | None = None
    host_id: UUID | None = None
    badge: str | None = None
    vehicle: str | None = None


class Gate:
    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, clock: Clock, school_time: SchoolTime
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._school_time = school_time

    # ---- collectors -------------------------------------------------------------------------

    async def add_collector(
        self, actor: Principal, student_id: UUID, cmd: CollectorCommand
    ) -> CollectorView:
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        today = (await self._school_time.now_local(tenant_id)).date()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await self._may_edit(uow, actor, student_id)
            if len(await uow.gate.collectors_of(student_id, include_inactive=False)) >= 10:
                raise ValidationFailed(
                    "A student can have at most 10 authorised collectors.", code="too_many"
                )
            collector = Collector.add(
                tenant_id=tenant_id,
                student_id=student_id,
                name=cmd.name,
                relationship=cmd.relationship,
                phone=cmd.phone,
                id_document=cmd.id_document,
                valid_on=cmd.valid_on,
                today=today,
                by=actor.user_id,
                now=now,
            )
            await uow.gate.add_collector(collector)
            uow.audit(
                audit.by(
                    actor,
                    "gate.collector_added",
                    target_type="school_user",
                    target_id=student_id,
                    collector=collector.name,
                    relationship=collector.relationship,
                    valid_on=str(collector.valid_on) if collector.valid_on else None,
                )
            )
            await uow.commit()
        return CollectorView.of(collector)

    async def remove_collector(self, actor: Principal, collector_id: UUID) -> CollectorView:
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            collector = await uow.gate.get_collector(collector_id)
            if collector is None:
                raise NotFound("Collector not found.")
            await self._may_edit(uow, actor, collector.student_id)
            collector.active = False
            await uow.gate.save_collector(collector)
            uow.audit(
                audit.by(
                    actor,
                    "gate.collector_removed",
                    target_type="school_user",
                    target_id=collector.student_id,
                    collector=collector.name,
                )
            )
            await uow.commit()
        return CollectorView.of(collector)

    async def collectors(self, actor: Principal, student_id: UUID) -> list[CollectorView]:
        """The full list for a guardian or the office, including one-offs and removed ones."""
        actor.require_realm(Realm.SCHOOL)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            await self._may_edit(uow, actor, student_id)
            found = await uow.gate.collectors_of(student_id, include_inactive=True)
        return [CollectorView.of(c) for c in found]

    # ---- releases ---------------------------------------------------------------------------

    async def pickup_list(self, actor: Principal, student_id: UUID) -> PickupList:
        actor.require_realm(Realm.SCHOOL)
        actor.require("gate:manage")
        tenant_id = actor.school_tenant_id
        today = (await self._school_time.now_local(tenant_id)).date()
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            name = await _student(uow, tenant_id, student_id)
            await serve_student(uow, actor, student_id, SectionFeature.GATE)
            links = await uow.guardians.guardians_of(student_id)
            names = await uow.users.school_names({g.parent_id for g in links})
            collectors = await uow.gate.collectors_of(student_id, include_inactive=False)
        return PickupList(
            student_id=student_id,
            student_name=name,
            guardians=[
                GuardianOption(
                    id=g.parent_id,
                    name=names.get(g.parent_id, ""),
                    relationship=g.relationship.value,
                )
                for g in links
            ],
            collectors=[CollectorView.of(c) for c in collectors if c.allowed_on(today)],
        )

    async def release(self, actor: Principal, cmd: ReleaseCommand) -> ReleaseView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("gate:manage")
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        today = (await self._school_time.now_local(tenant_id)).date()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            name = await _student(uow, tenant_id, cmd.student_id)
            await serve_student(uow, actor, cmd.student_id, SectionFeature.GATE)
            guardians = {g.parent_id for g in await uow.guardians.guardians_of(cmd.student_id)}
            if cmd.guardian_id is not None and cmd.guardian_id not in guardians:
                raise ValidationFailed(
                    "That person isn't this student's guardian.", code="not_authorised"
                )
            collector = None
            if cmd.collector_id is not None:
                collector = await uow.gate.get_collector(cmd.collector_id)
                if collector is None:
                    raise ValidationFailed(
                        "That person isn't authorised to collect this student today.",
                        code="not_authorised",
                    )
            release = Release.decide(
                tenant_id=tenant_id,
                student_id=cmd.student_id,
                guardian_id=cmd.guardian_id,
                collector=collector,
                refused_name=cmd.refused_name,
                note=cmd.note,
                today=today,
                by=actor.user_id,
                now=now,
            )
            if release.guardian_id is not None:
                names = await uow.users.school_names({release.guardian_id})
                release = _with_name(release, names.get(release.guardian_id, "Guardian"))
            await uow.gate.add_release(release)
            first = name.split()[0] if name else "Your child"
            refused = release.status is ReleaseStatus.REFUSED
            await uow.communication.notify(
                [
                    Notification.new(
                        tenant_id=tenant_id,
                        user_id=parent,
                        kind=NotificationKind.GENERAL,
                        title=(
                            f"Someone tried to collect {first}"
                            if refused
                            else f"{first} has left school"
                        ),
                        body=(
                            f"{release.collector_name} was not on the list, so {first} stayed "
                            "at school. Please call the school."
                            if refused
                            else f"Collected by {release.collector_name}."
                        ),
                        link_type="gate_release",
                        link_id=release.id,
                        now=now,
                    )
                    for parent in sorted(guardians)
                ]
            )
            uow.audit(
                audit.by(
                    actor,
                    "gate.student_refused" if refused else "gate.student_released",
                    target_type="school_user",
                    target_id=cmd.student_id,
                    collector=release.collector_name,
                    note=release.note,
                )
            )
            await uow.commit()
        return _release_view(release, name)

    async def releases(
        self,
        actor: Principal,
        *,
        student_id: UUID | None,
        cursor: str | None,
        limit: int | None,
    ) -> Page[ReleaseView]:
        """Gate staff and leaders see all (the last 90 days); guardians see their child's."""
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            family = (
                student_id is not None
                and SchoolRole.PARENT.value in actor.roles
                and await uow.guardians.is_guardian(actor.user_id, student_id)
            )
            if not family:
                actor.require("gate:read")
                if student_id is not None:
                    await ensure_student_in_scope(uow, actor, student_id)
            page = await uow.gate.releases(
                student_id=student_id,
                since=self._clock.now() - timedelta(days=90),
                cursor=cursor,
                limit=clamp_limit(limit),
            )
            names = await uow.users.school_names({r.student_id for r in page.items})
            shown = (
                {r.student_id for r in page.items}
                if family
                else await visible_students(uow, actor, {r.student_id for r in page.items})
            )
        return Page(
            items=[
                _release_view(r, names.get(r.student_id, ""))
                for r in page.items
                if r.student_id in shown
            ],
            next_cursor=page.next_cursor,
        )

    # ---- visitors ---------------------------------------------------------------------------

    async def sign_in(self, actor: Principal, cmd: VisitorCommand) -> VisitView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("gate:manage")
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            host_name = None
            if cmd.host_id is not None:
                host = await uow.users.get(cmd.host_id)
                if (
                    host is None
                    or host.tenant_id != tenant_id
                    or not host.roles
                    - {
                        _STUDENT,
                        SchoolRole.PARENT.value,
                    }
                ):
                    raise ValidationFailed("Pick a member of staff.", details={"field": "host_id"})
                host_name = host.full_name
            visit = Visit.sign_in(
                tenant_id=tenant_id,
                name=cmd.name,
                phone=cmd.phone,
                organisation=cmd.organisation,
                purpose=cmd.purpose,
                host_id=cmd.host_id,
                badge=cmd.badge,
                vehicle=cmd.vehicle,
                by=actor.user_id,
                now=now,
            )
            await uow.gate.add_visit(visit)
            if cmd.host_id is not None:
                await uow.communication.notify(
                    [
                        Notification.new(
                            tenant_id=tenant_id,
                            user_id=cmd.host_id,
                            kind=NotificationKind.GENERAL,
                            title=f"{visit.name} is here to see you",
                            body=visit.purpose,
                            link_type="gate_visit",
                            link_id=visit.id,
                            now=now,
                        )
                    ]
                )
            uow.audit(
                audit.by(
                    actor,
                    "gate.visitor_signed_in",
                    target_type="gate_visit",
                    target_id=visit.id,
                    visitor=visit.name,
                    purpose=visit.purpose,
                )
            )
            await uow.commit()
        return _visit_view(visit, host_name)

    async def sign_out(self, actor: Principal, visit_id: UUID) -> VisitView:
        actor.require_realm(Realm.SCHOOL)
        actor.require("gate:manage")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            visit = await uow.gate.get_visit(visit_id, for_update=True)
            if visit is None:
                raise NotFound("Visit not found.")
            visit.sign_out(by=actor.user_id, now=self._clock.now())
            await uow.gate.save_visit(visit)
            uow.audit(
                audit.by(
                    actor,
                    "gate.visitor_signed_out",
                    target_type="gate_visit",
                    target_id=visit.id,
                    visitor=visit.name,
                )
            )
            names = await uow.users.school_names({visit.host_id} if visit.host_id else set())
            await uow.commit()
        return _visit_view(visit, names.get(visit.host_id) if visit.host_id else None)

    async def visits(
        self, actor: Principal, *, on_site_only: bool, cursor: str | None, limit: int | None
    ) -> Page[VisitView]:
        """Newest first, the last 90 days. ``on_site_only`` is the fire-register view."""
        actor.require_realm(Realm.SCHOOL)
        actor.require("gate:read")
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            page = await uow.gate.visits(
                on_site_only=on_site_only,
                since=None if on_site_only else self._clock.now() - timedelta(days=90),
                cursor=cursor,
                limit=clamp_limit(limit),
            )
            names = await uow.users.school_names({v.host_id for v in page.items if v.host_id})
        return Page(
            items=[_visit_view(v, names.get(v.host_id) if v.host_id else None) for v in page.items],
            next_cursor=page.next_cursor,
        )

    # ---- helpers ----------------------------------------------------------------------------

    @staticmethod
    async def _may_edit(uow: UnitOfWork, actor: Principal, student_id: UUID) -> None:
        await _student(uow, actor.school_tenant_id, student_id)
        if SchoolRole.PARENT.value in actor.roles and await uow.guardians.is_guardian(
            actor.user_id, student_id
        ):
            return
        if not actor.permissions.allows("students:update"):
            raise PermissionDenied("Only the student's guardians or the school office can do this.")
        await ensure_student_in_scope(uow, actor, student_id)


async def _student(uow: UnitOfWork, tenant_id: UUID, student_id: UUID) -> str:
    user = await uow.users.get(student_id)
    if user is None or user.tenant_id != tenant_id or _STUDENT not in user.roles:
        raise NotFound("Student not found.")
    return user.full_name


def _with_name(release: Release, name: str) -> Release:
    return Release(
        id=release.id,
        tenant_id=release.tenant_id,
        student_id=release.student_id,
        status=release.status,
        guardian_id=release.guardian_id,
        collector_id=release.collector_id,
        collector_name=name,
        note=release.note,
        by=release.by,
        at=release.at,
    )


def _release_view(r: Release, name: str) -> ReleaseView:
    return ReleaseView(
        id=r.id,
        student_id=r.student_id,
        student_name=name,
        status=r.status.value,
        guardian_id=r.guardian_id,
        collector_id=r.collector_id,
        collector_name=r.collector_name,
        note=r.note,
        by=r.by,
        at=r.at,
    )


def _visit_view(v: Visit, host_name: str | None) -> VisitView:
    return VisitView(
        id=v.id,
        name=v.name,
        phone=v.phone,
        organisation=v.organisation,
        purpose=v.purpose,
        host_id=v.host_id,
        host_name=host_name,
        badge=v.badge,
        vehicle=v.vehicle,
        signed_in_at=v.signed_in_at,
        signed_out_at=v.signed_out_at,
    )
