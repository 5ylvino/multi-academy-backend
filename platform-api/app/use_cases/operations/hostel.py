"""Boarding: hostels and rooms, where each boarder sleeps, and exeats.

- Boarding staff (``hostel:manage``: by default the director, school admin, head teacher,
  principal and vice principal; grant it to a house master or mistress) set up hostels and rooms
  and allocate boarders to rooms and beds.
- A guardian asks for an exeat for their child (who leaves, with whom, until when); boarding
  staff can also raise one. Someone other than the person who asked approves or rejects it.
  The hostel signs the boarder out and back in. Guardians are told at each step; the hostel's
  warden is told when a family asks.
- Guardians and the boarder see the room, the warden and their exeats.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.academics.sections import SectionFeature
from app.domain.communication.inbox import Notification, NotificationKind
from app.domain.identity.roles import Realm, SchoolRole
from app.domain.operations.hostel import (
    Allocation,
    Exeat,
    ExeatStatus,
    Hostel,
    HostelKind,
    Room,
    bed_label,
)
from app.domain.shared.errors import (
    InvalidStateTransition,
    NotFound,
    ValidationFailed,
)
from app.domain.shared.value_objects import Page
from app.use_cases.academics.sections import (
    ensure_student_in_scope,
    serve_student,
    visible_students,
)
from app.use_cases.identity.guardians import is_guardian_or_self
from app.use_cases.shared import audit
from app.use_cases.shared.pagination import clamp_limit
from app.use_cases.shared.ports import Clock, Scope, UnitOfWork, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal

MANAGE = "hostel:manage"
_STUDENT = SchoolRole.STUDENT.value
_FAMILY = {SchoolRole.STUDENT.value, SchoolRole.PARENT.value}


@dataclass(frozen=True, slots=True)
class RoomView:
    id: UUID
    name: str
    capacity: int
    occupants: int


@dataclass(frozen=True, slots=True)
class HostelView:
    id: UUID
    name: str
    kind: str
    warden_id: UUID | None
    warden_name: str | None
    active: bool
    beds: int
    boarders: int
    rooms: list[RoomView]


@dataclass(frozen=True, slots=True)
class ResidentView:
    student_id: UUID
    student_name: str
    room_id: UUID
    room_name: str
    bed: str | None
    away: bool


@dataclass(frozen=True, slots=True)
class ExeatView:
    id: UUID
    student_id: UUID
    student_name: str
    reason: str
    collector_name: str
    leave_at: datetime
    return_by: datetime
    status: str
    requested_by: UUID
    requested_by_name: str
    decided_by: UUID | None
    decided_at: datetime | None
    decision_note: str | None
    left_at: datetime | None
    returned_at: datetime | None
    overdue: bool
    returned_late: bool


@dataclass(frozen=True, slots=True)
class BoarderView:
    student_id: UUID
    student_name: str
    hostel_id: UUID
    hostel_name: str
    room_id: UUID
    room_name: str
    bed: str | None
    warden_name: str | None
    exeat: ExeatView | None


@dataclass(frozen=True, slots=True)
class ExeatCommand:
    reason: str
    collector_name: str
    leave_at: datetime
    return_by: datetime


class Hostels:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    # ---- hostels and rooms ------------------------------------------------------------------

    async def create_hostel(
        self, actor: Principal, *, name: str, kind: str, warden_id: UUID | None
    ) -> HostelView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        tenant_id = actor.school_tenant_id
        try:
            hostel_kind = HostelKind(kind)
        except ValueError as exc:
            raise ValidationFailed("Unknown hostel type.", details={"field": "kind"}) from exc
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await _check_staff(uow, tenant_id, warden_id)
            hostel = Hostel.create(
                tenant_id=tenant_id,
                name=name,
                kind=hostel_kind,
                warden_id=warden_id,
                now=self._clock.now(),
            )
            if any(
                h.name.lower() == hostel.name.lower()
                for h in await uow.hostel.hostels(include_inactive=True)
            ):
                raise ValidationFailed(
                    "Another hostel has that name.", details={"field": "name"}, code="name_taken"
                )
            await uow.hostel.add_hostel(hostel)
            uow.audit(audit.by(actor, "hostel.created", target_type="hostel", target_id=hostel.id))
            view = await _hostel_view(uow, hostel, {})
            await uow.commit()
        return view

    async def update_hostel(
        self,
        actor: Principal,
        hostel_id: UUID,
        *,
        name: str,
        warden_id: UUID | None,
        active: bool,
    ) -> HostelView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            hostel = await _load_hostel(uow, hostel_id)
            await _check_staff(uow, tenant_id, warden_id)
            occupancy = await uow.hostel.occupancy()
            residents = sum(occupancy.get(r.id, 0) for r in await uow.hostel.rooms(hostel.id))
            hostel.change(name=name, warden_id=warden_id, active=active, residents=residents)
            await uow.hostel.save_hostel(hostel)
            uow.audit(
                audit.by(
                    actor,
                    "hostel.updated",
                    target_type="hostel",
                    target_id=hostel.id,
                    active=hostel.active,
                )
            )
            view = await _hostel_view(uow, hostel, occupancy)
            await uow.commit()
        return view

    async def hostels(
        self, actor: Principal, *, include_inactive: bool = False
    ) -> list[HostelView]:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            occupancy = await uow.hostel.occupancy()
            return [
                await _hostel_view(uow, h, occupancy)
                for h in await uow.hostel.hostels(include_inactive=include_inactive)
            ]

    async def add_room(
        self, actor: Principal, hostel_id: UUID, *, name: str, capacity: int
    ) -> RoomView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            hostel = await _load_hostel(uow, hostel_id)
            if not hostel.active:
                raise InvalidStateTransition("This hostel is closed.", code="hostel_inactive")
            room = Room.add(
                tenant_id=hostel.tenant_id, hostel_id=hostel.id, name=name, capacity=capacity
            )
            await _unique_room(uow, room)
            await uow.hostel.add_room(room)
            uow.audit(
                audit.by(
                    actor,
                    "hostel.room_added",
                    target_type="hostel",
                    target_id=hostel.id,
                    room_id=str(room.id),
                )
            )
            await uow.commit()
        return RoomView(id=room.id, name=room.name, capacity=room.capacity, occupants=0)

    async def update_room(
        self, actor: Principal, room_id: UUID, *, name: str, capacity: int, active: bool
    ) -> RoomView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            room = await _load_room(uow, room_id, for_update=True)
            occupants = (await uow.hostel.occupancy()).get(room.id, 0)
            room.change(name=name, capacity=capacity, active=active, occupants=occupants)
            if room.active:
                await _unique_room(uow, room)
            await uow.hostel.save_room(room)
            uow.audit(
                audit.by(
                    actor,
                    "hostel.room_updated",
                    target_type="hostel",
                    target_id=room.hostel_id,
                    room_id=str(room.id),
                )
            )
            await uow.commit()
        return RoomView(id=room.id, name=room.name, capacity=room.capacity, occupants=occupants)

    # ---- boarders ---------------------------------------------------------------------------

    async def allocate(
        self, actor: Principal, student_id: UUID, *, room_id: UUID, bed: str | None
    ) -> BoarderView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        tenant_id = actor.school_tenant_id
        label = bed_label(bed)
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await _student(uow, tenant_id, student_id)
            await serve_student(uow, actor, student_id, SectionFeature.BOARDING)
            room = await _load_room(uow, room_id, for_update=True)  # serialises bed checks
            hostel = await _load_hostel(uow, room.hostel_id)
            if not hostel.active:
                raise InvalidStateTransition("This hostel is closed.", code="hostel_inactive")
            current = await uow.hostel.allocation(student_id)
            moving_in = current is None or current.room_id != room.id
            if moving_in and (await uow.hostel.occupancy()).get(room.id, 0) >= room.capacity:
                raise ValidationFailed(
                    f"All {room.capacity} beds in {room.name} are taken.", code="room_full"
                )
            if label is not None:
                holder = await uow.hostel.bed_holder(room.id, label)
                if holder is not None and holder != student_id:
                    raise ValidationFailed(
                        "Someone already has that bed.", details={"field": "bed"}, code="bed_taken"
                    )
            await uow.hostel.save_allocation(
                Allocation(
                    tenant_id=tenant_id,
                    student_id=student_id,
                    room_id=room.id,
                    bed=label,
                    by=actor.user_id,
                    at=self._clock.now(),
                )
            )
            uow.audit(
                audit.by(
                    actor,
                    "hostel.boarder_allocated",
                    target_type="school_user",
                    target_id=student_id,
                    room_id=str(room.id),
                    previous_room_id=str(current.room_id) if current else None,
                )
            )
            view = await self._boarder(uow, tenant_id, student_id)
            await uow.commit()
        if view is None:  # pragma: no cover - just allocated
            raise NotFound("Not a boarder.")
        return view

    async def vacate(self, actor: Principal, student_id: UUID) -> None:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            await ensure_student_in_scope(uow, actor, student_id)
            current = await uow.hostel.allocation(student_id)
            if current is None:
                raise NotFound("This student isn't a boarder.")
            if await uow.hostel.open_exeat(student_id) is not None:
                raise InvalidStateTransition("Close this boarder's exeat first.", code="exeat_open")
            await uow.hostel.remove_allocation(student_id)
            uow.audit(
                audit.by(
                    actor,
                    "hostel.boarder_vacated",
                    target_type="school_user",
                    target_id=student_id,
                    room_id=str(current.room_id),
                )
            )
            await uow.commit()

    async def residents(self, actor: Principal, hostel_id: UUID) -> list[ResidentView]:
        """Boarders by room, and who is away on an exeat now (the evening roll call)."""
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            hostel = await _load_hostel(uow, hostel_id)
            rooms = {r.id: r for r in await uow.hostel.rooms(hostel.id)}
            found = await uow.hostel.allocations(hostel.id)
            shown = await visible_students(uow, actor, {a.student_id for a in found})
            found = [a for a in found if a.student_id in shown]
            names = await uow.users.school_names({a.student_id for a in found})
            away = await uow.hostel.students_out()
        rows = [
            ResidentView(
                student_id=a.student_id,
                student_name=names.get(a.student_id, ""),
                room_id=a.room_id,
                room_name=rooms[a.room_id].name if a.room_id in rooms else "",
                bed=a.bed,
                away=a.student_id in away,
            )
            for a in found
        ]
        return sorted(rows, key=lambda r: (r.room_name, r.bed or "", r.student_name))

    async def boarder(self, actor: Principal, student_id: UUID) -> BoarderView | None:
        """The boarder's room and current exeat. None for a day student."""
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            await _student(uow, tenant_id, student_id)
            if not await is_guardian_or_self(uow, actor, student_id):
                actor.require(MANAGE)
                await ensure_student_in_scope(uow, actor, student_id)
            return await self._boarder(uow, tenant_id, student_id)

    # ---- exeats -----------------------------------------------------------------------------

    async def request_exeat(
        self, actor: Principal, student_id: UUID, cmd: ExeatCommand
    ) -> ExeatView:
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            name = await _student(uow, tenant_id, student_id)
            family = SchoolRole.PARENT.value in actor.roles and await uow.guardians.is_guardian(
                actor.user_id, student_id
            )
            if not family:
                actor.require(MANAGE)
            allocation = await uow.hostel.allocation(student_id)
            if allocation is None:
                raise ValidationFailed("This student isn't a boarder.", code="not_a_boarder")
            if await uow.hostel.open_exeat(student_id) is not None:
                raise InvalidStateTransition(
                    "There is already an exeat open for this boarder.", code="exeat_open"
                )
            exeat = Exeat.request(
                tenant_id=tenant_id,
                student_id=student_id,
                reason=cmd.reason,
                collector_name=cmd.collector_name,
                leave_at=cmd.leave_at,
                return_by=cmd.return_by,
                by=actor.user_id,
                now=now,
            )
            await uow.hostel.add_exeat(exeat)
            room = await uow.hostel.get_room(allocation.room_id)
            hostel = await uow.hostel.get_hostel(room.hostel_id) if room else None
            first = _first(name)
            if family and hostel and hostel.warden_id:
                await uow.communication.notify(
                    [
                        Notification.new(
                            tenant_id=tenant_id,
                            user_id=hostel.warden_id,
                            kind=NotificationKind.GENERAL,
                            title=f"Exeat request for {name}",
                            body=f"{first} would leave with {exeat.collector_name}.",
                            link_type="hostel_exeat",
                            link_id=exeat.id,
                            now=now,
                        )
                    ]
                )
            uow.audit(
                audit.by(
                    actor,
                    "hostel.exeat_requested",
                    target_type="hostel_exeat",
                    target_id=exeat.id,
                    student_id=str(student_id),
                )
            )
            view = await _exeat_view(uow, exeat, now)
            await uow.commit()
        return view

    async def decide(
        self, actor: Principal, exeat_id: UUID, *, approve: bool, note: str | None
    ) -> ExeatView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            exeat = await _load_exeat(uow, exeat_id)
            await ensure_student_in_scope(uow, actor, exeat.student_id)
            if approve:
                exeat.approve(by=actor.user_id, note=note, now=now)
            else:
                exeat.reject(by=actor.user_id, note=note or "", now=now)
            await uow.hostel.save_exeat(exeat)
            name = await _student(uow, actor.school_tenant_id, exeat.student_id)
            first = _first(name)
            if approve:
                title = f"Exeat approved for {first}"
                body = f"{first} may leave with {exeat.collector_name}."
            else:
                title = f"Exeat not approved for {first}"
                body = exeat.decision_note or ""
            await _tell_guardians(uow, exeat, title=title, body=body, now=now)
            uow.audit(
                audit.by(
                    actor,
                    "hostel.exeat_approved" if approve else "hostel.exeat_rejected",
                    target_type="hostel_exeat",
                    target_id=exeat.id,
                )
            )
            view = await _exeat_view(uow, exeat, now)
            await uow.commit()
        return view

    async def cancel(self, actor: Principal, exeat_id: UUID) -> ExeatView:
        """The person who asked, or boarding staff, before the boarder leaves."""
        actor.require_realm(Realm.SCHOOL)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            exeat = await _load_exeat(uow, exeat_id)
            if exeat.requested_by != actor.user_id:
                actor.require(MANAGE)
                await ensure_student_in_scope(uow, actor, exeat.student_id)
            exeat.cancel(now=now)
            await uow.hostel.save_exeat(exeat)
            uow.audit(
                audit.by(
                    actor, "hostel.exeat_cancelled", target_type="hostel_exeat", target_id=exeat.id
                )
            )
            view = await _exeat_view(uow, exeat, now)
            await uow.commit()
        return view

    async def sign_out(self, actor: Principal, exeat_id: UUID) -> ExeatView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            exeat = await _load_exeat(uow, exeat_id)
            await ensure_student_in_scope(uow, actor, exeat.student_id)
            exeat.sign_out(now=now)
            await uow.hostel.save_exeat(exeat)
            first = _first(await _student(uow, actor.school_tenant_id, exeat.student_id))
            await _tell_guardians(
                uow,
                exeat,
                title=f"{first} has left the hostel",
                body=f"With {exeat.collector_name}.",
                now=now,
            )
            uow.audit(
                audit.by(
                    actor, "hostel.exeat_signed_out", target_type="hostel_exeat", target_id=exeat.id
                )
            )
            view = await _exeat_view(uow, exeat, now)
            await uow.commit()
        return view

    async def sign_in(self, actor: Principal, exeat_id: UUID) -> ExeatView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            exeat = await _load_exeat(uow, exeat_id)
            await ensure_student_in_scope(uow, actor, exeat.student_id)
            exeat.sign_in(now=now)
            await uow.hostel.save_exeat(exeat)
            first = _first(await _student(uow, actor.school_tenant_id, exeat.student_id))
            await _tell_guardians(
                uow,
                exeat,
                title=f"{first} is back at the hostel",
                body="Returned late." if exeat.returned_late else "Returned on time.",
                now=now,
            )
            uow.audit(
                audit.by(
                    actor,
                    "hostel.exeat_returned",
                    target_type="hostel_exeat",
                    target_id=exeat.id,
                    late=exeat.returned_late,
                )
            )
            view = await _exeat_view(uow, exeat, now)
            await uow.commit()
        return view

    async def exeats(
        self,
        actor: Principal,
        *,
        student_id: UUID | None,
        status: str | None,
        overdue: bool,
        cursor: str | None,
        limit: int | None,
    ) -> Page[ExeatView]:
        """Boarding staff see all; guardians and the boarder pass the student's id."""
        actor.require_realm(Realm.SCHOOL)
        statuses = None
        if status:
            try:
                statuses = frozenset({ExeatStatus(status)})
            except ValueError as exc:
                raise ValidationFailed("Unknown status.", details={"field": "status"}) from exc
        now = self._clock.now()
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            if student_id is None or not await is_guardian_or_self(uow, actor, student_id):
                actor.require(MANAGE)
            page = await uow.hostel.exeats(
                student_id=student_id,
                statuses=statuses,
                overdue_at=now if overdue else None,
                cursor=cursor,
                limit=clamp_limit(limit),
            )
            names = await uow.users.school_names(
                {e.student_id for e in page.items} | {e.requested_by for e in page.items}
            )
        return Page(
            items=[_exeat_view_named(e, names, now) for e in page.items],
            next_cursor=page.next_cursor,
        )

    # ---- helpers ----------------------------------------------------------------------------

    async def _boarder(
        self, uow: UnitOfWork, tenant_id: UUID, student_id: UUID
    ) -> BoarderView | None:
        a = await uow.hostel.allocation(student_id)
        if a is None:
            return None
        room = await uow.hostel.get_room(a.room_id)
        hostel = await uow.hostel.get_hostel(room.hostel_id) if room else None
        if room is None or hostel is None:  # pragma: no cover - foreign keys
            return None
        ids = {student_id} | ({hostel.warden_id} if hostel.warden_id else set())
        names = await uow.users.school_names(ids)
        exeat = await uow.hostel.open_exeat(student_id)
        now = self._clock.now()
        return BoarderView(
            student_id=student_id,
            student_name=names.get(student_id, ""),
            hostel_id=hostel.id,
            hostel_name=hostel.name,
            room_id=room.id,
            room_name=room.name,
            bed=a.bed,
            warden_name=names.get(hostel.warden_id) if hostel.warden_id else None,
            exeat=await _exeat_view(uow, exeat, now) if exeat else None,
        )


def _first(name: str) -> str:
    return name.split()[0] if name else "Your child"


async def _tell_guardians(
    uow: UnitOfWork, exeat: Exeat, *, title: str, body: str, now: datetime
) -> None:
    guardians = await uow.guardians.guardians_of(exeat.student_id)
    await uow.communication.notify(
        [
            Notification.new(
                tenant_id=exeat.tenant_id,
                user_id=g.parent_id,
                kind=NotificationKind.GENERAL,
                title=title,
                body=body,
                link_type="hostel_exeat",
                link_id=exeat.id,
                now=now,
            )
            for g in guardians
        ]
    )


async def _check_staff(uow: UnitOfWork, tenant_id: UUID, user_id: UUID | None) -> None:
    if user_id is None:
        return
    user = await uow.users.get(user_id)
    if user is None or user.tenant_id != tenant_id or not user.roles - _FAMILY:
        raise ValidationFailed("Pick a member of staff.", details={"field": "warden_id"})


async def _unique_room(uow: UnitOfWork, room: Room) -> None:
    for other in await uow.hostel.rooms(room.hostel_id):
        if other.id != room.id and other.name.lower() == room.name.lower():
            raise ValidationFailed(
                "Another room in this hostel has that name.",
                details={"field": "name"},
                code="name_taken",
            )


async def _load_hostel(uow: UnitOfWork, hostel_id: UUID) -> Hostel:
    hostel = await uow.hostel.get_hostel(hostel_id)
    if hostel is None:
        raise NotFound("Hostel not found.")
    return hostel


async def _load_room(uow: UnitOfWork, room_id: UUID, *, for_update: bool = False) -> Room:
    room = await uow.hostel.get_room(room_id, for_update=for_update)
    if room is None or not room.active:
        raise NotFound("Room not found.")
    return room


async def _load_exeat(uow: UnitOfWork, exeat_id: UUID) -> Exeat:
    exeat = await uow.hostel.get_exeat(exeat_id, for_update=True)
    if exeat is None:
        raise NotFound("Exeat not found.")
    return exeat


async def _student(uow: UnitOfWork, tenant_id: UUID, student_id: UUID) -> str:
    user = await uow.users.get(student_id)
    if user is None or user.tenant_id != tenant_id or _STUDENT not in user.roles:
        raise NotFound("Student not found.")
    return user.full_name


async def _hostel_view(uow: UnitOfWork, hostel: Hostel, occupancy: dict[UUID, int]) -> HostelView:
    rooms = [
        RoomView(id=r.id, name=r.name, capacity=r.capacity, occupants=occupancy.get(r.id, 0))
        for r in await uow.hostel.rooms(hostel.id)
    ]
    warden = (
        (await uow.users.school_names({hostel.warden_id})).get(hostel.warden_id)
        if hostel.warden_id
        else None
    )
    return HostelView(
        id=hostel.id,
        name=hostel.name,
        kind=hostel.kind.value,
        warden_id=hostel.warden_id,
        warden_name=warden,
        active=hostel.active,
        beds=sum(r.capacity for r in rooms),
        boarders=sum(r.occupants for r in rooms),
        rooms=rooms,
    )


async def _exeat_view(uow: UnitOfWork, exeat: Exeat, now: datetime) -> ExeatView:
    names = await uow.users.school_names({exeat.student_id, exeat.requested_by})
    return _exeat_view_named(exeat, names, now)


def _exeat_view_named(exeat: Exeat, names: dict[UUID, str], now: datetime) -> ExeatView:
    return ExeatView(
        id=exeat.id,
        student_id=exeat.student_id,
        student_name=names.get(exeat.student_id, ""),
        reason=exeat.reason,
        collector_name=exeat.collector_name,
        leave_at=exeat.leave_at,
        return_by=exeat.return_by,
        status=exeat.status.value,
        requested_by=exeat.requested_by,
        requested_by_name=names.get(exeat.requested_by, ""),
        decided_by=exeat.decided_by,
        decided_at=exeat.decided_at,
        decision_note=exeat.decision_note,
        left_at=exeat.left_at,
        returned_at=exeat.returned_at,
        overdue=exeat.overdue(now),
        returned_late=exeat.returned_late,
    )
