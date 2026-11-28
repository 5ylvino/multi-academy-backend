"""School transport: routes, stops, who rides where, and the driver's boarding record.

- The office (``transport:manage``) sets up routes with a vehicle, seats and a driver, the stops
  in order with pickup and drop-off times, and puts each student on a route and stop.
- The driver (``transport:board``) sees the day's manifest for their own route and records each
  child boarding, being dropped off, or not being at the stop. Guardians are told each time.
- Staff with ``transport:read`` see routes and manifests; guardians and students see their own
  route, stop and the trip history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID

from app.domain.academics.sections import SectionFeature
from app.domain.communication.inbox import Notification, NotificationKind
from app.domain.identity.roles import Realm, SchoolRole
from app.domain.operations.transport import (
    Assignment,
    Boarding,
    BoardingKind,
    Direction,
    Route,
    Stop,
)
from app.domain.shared.errors import (
    InvalidStateTransition,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from app.domain.shared.value_objects import Page
from app.use_cases.academics.sections import ensure_student_in_scope, serve_student
from app.use_cases.identity.guardians import is_guardian_or_self
from app.use_cases.shared import audit
from app.use_cases.shared.pagination import clamp_limit
from app.use_cases.shared.ports import Clock, SchoolTime, Scope, UnitOfWork, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal

MANAGE = "transport:manage"
READ = "transport:read"
BOARD = "transport:board"
_STUDENT = SchoolRole.STUDENT.value
_FAMILY = {SchoolRole.STUDENT.value, SchoolRole.PARENT.value}


@dataclass(frozen=True, slots=True)
class StopView:
    id: UUID
    name: str
    position: int
    morning_at: time | None
    afternoon_at: time | None
    landmark: str | None


@dataclass(frozen=True, slots=True)
class RouteView:
    id: UUID
    name: str
    code: str
    vehicle_plate: str | None
    capacity: int
    riders: int
    driver_id: UUID | None
    driver_name: str | None
    active: bool
    stops: list[StopView]


@dataclass(frozen=True, slots=True)
class TripStatus:
    kind: str
    at: datetime


@dataclass(frozen=True, slots=True)
class StudentTransport:
    student_id: UUID
    student_name: str
    route_id: UUID
    route_name: str
    route_code: str
    vehicle_plate: str | None
    driver_name: str | None
    stop: StopView
    to_school: TripStatus | None
    from_school: TripStatus | None


@dataclass(frozen=True, slots=True)
class ManifestRow:
    student_id: UUID
    student_name: str
    stop_id: UUID
    stop_name: str
    position: int
    status: str | None
    at: datetime | None


@dataclass(frozen=True, slots=True)
class Manifest:
    route_id: UUID
    route_code: str
    route_name: str
    direction: str
    day: date
    riders: list[ManifestRow]
    on_board: int


@dataclass(frozen=True, slots=True)
class BoardingView:
    id: UUID
    student_id: UUID
    route_id: UUID
    stop_id: UUID | None
    direction: str
    kind: str
    day: date
    at: datetime


@dataclass(frozen=True, slots=True)
class RouteCommand:
    name: str
    vehicle_plate: str | None
    capacity: int
    driver_id: UUID | None
    code: str = ""
    active: bool = True


@dataclass(frozen=True, slots=True)
class StopCommand:
    name: str
    position: int
    morning_at: time | None = None
    afternoon_at: time | None = None
    landmark: str | None = None


class Transport:
    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, clock: Clock, school_time: SchoolTime
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._school_time = school_time

    # ---- routes -----------------------------------------------------------------------------

    async def create_route(self, actor: Principal, cmd: RouteCommand) -> RouteView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await _check_driver(uow, tenant_id, cmd.driver_id)
            route = Route.create(
                tenant_id=tenant_id,
                name=cmd.name,
                code=cmd.code,
                vehicle_plate=cmd.vehicle_plate,
                capacity=cmd.capacity,
                driver_id=cmd.driver_id,
                now=self._clock.now(),
            )
            if await uow.transport.route_by_code(route.code) is not None:
                raise ValidationFailed(
                    "Another route uses that code.", details={"field": "code"}, code="code_taken"
                )
            await uow.transport.add_route(route)
            uow.audit(
                audit.by(
                    actor,
                    "transport.route_created",
                    target_type="transport_route",
                    target_id=route.id,
                    code=route.code,
                )
            )
            view = await _route_view(uow, route, riders=0)
            await uow.commit()
        return view

    async def update_route(self, actor: Principal, route_id: UUID, cmd: RouteCommand) -> RouteView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            route = await _load_route(uow, route_id, for_update=True)
            await _check_driver(uow, tenant_id, cmd.driver_id)
            riders = (await uow.transport.rider_counts()).get(route.id, 0)
            before = (
                route.name,
                route.vehicle_plate,
                route.capacity,
                route.driver_id,
                route.active,
            )
            route.change(
                name=cmd.name,
                vehicle_plate=cmd.vehicle_plate,
                capacity=cmd.capacity,
                driver_id=cmd.driver_id,
                active=cmd.active,
                riders=riders,
            )
            await uow.transport.save_route(route)
            after = (route.name, route.vehicle_plate, route.capacity, route.driver_id, route.active)
            names = ("name", "vehicle_plate", "capacity", "driver_id", "active")
            uow.audit(
                audit.by(
                    actor,
                    "transport.route_updated",
                    target_type="transport_route",
                    target_id=route.id,
                    fields=[n for n, b, a in zip(names, before, after, strict=True) if b != a],
                )
            )
            view = await _route_view(uow, route, riders=riders)
            await uow.commit()
        return view

    async def routes(self, actor: Principal, *, include_inactive: bool = False) -> list[RouteView]:
        """Staff with ``transport:read``. A driver without it sees the routes they drive."""
        actor.require_realm(Realm.SCHOOL)
        _staff_only(actor)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            if actor.permissions.allows(READ):
                found = await uow.transport.routes(include_inactive=include_inactive)
            else:
                actor.require(BOARD)
                found = await uow.transport.routes_driven_by(actor.user_id)
            counts = await uow.transport.rider_counts()
            return [await _route_view(uow, r, riders=counts.get(r.id, 0)) for r in found]

    async def route(self, actor: Principal, route_id: UUID) -> RouteView:
        actor.require_realm(Realm.SCHOOL)
        _staff_only(actor)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id), read_only=True) as uow:
            route = await _load_route(uow, route_id)
            _may_see_route(actor, route)
            counts = await uow.transport.rider_counts()
            return await _route_view(uow, route, riders=counts.get(route.id, 0))

    # ---- stops ------------------------------------------------------------------------------

    async def add_stop(self, actor: Principal, route_id: UUID, cmd: StopCommand) -> StopView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            route = await _load_route(uow, route_id, for_update=True)
            if len(await uow.transport.stops(route.id)) >= 40:
                raise ValidationFailed("A route can have up to 40 stops.", code="too_many_stops")
            stop = Stop.add(
                tenant_id=route.tenant_id,
                route_id=route.id,
                name=cmd.name,
                position=cmd.position,
                morning_at=cmd.morning_at,
                afternoon_at=cmd.afternoon_at,
                landmark=cmd.landmark,
            )
            await uow.transport.add_stop(stop)
            uow.audit(
                audit.by(
                    actor,
                    "transport.stop_added",
                    target_type="transport_route",
                    target_id=route.id,
                    stop_id=str(stop.id),
                )
            )
            await uow.commit()
        return _stop_view(stop)

    async def update_stop(self, actor: Principal, stop_id: UUID, cmd: StopCommand) -> StopView:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            stop = await _load_stop(uow, stop_id)
            stop.change(
                name=cmd.name,
                position=cmd.position,
                morning_at=cmd.morning_at,
                afternoon_at=cmd.afternoon_at,
                landmark=cmd.landmark,
            )
            await uow.transport.save_stop(stop)
            uow.audit(
                audit.by(
                    actor,
                    "transport.stop_updated",
                    target_type="transport_route",
                    target_id=stop.route_id,
                    stop_id=str(stop.id),
                )
            )
            await uow.commit()
        return _stop_view(stop)

    async def remove_stop(self, actor: Principal, stop_id: UUID) -> None:
        """Retires the stop. Its boarding history stays."""
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            stop = await _load_stop(uow, stop_id)
            if await uow.transport.stop_in_use(stop.id):
                raise InvalidStateTransition(
                    "Move the students who use this stop first.", code="stop_in_use"
                )
            stop.active = False
            await uow.transport.save_stop(stop)
            uow.audit(
                audit.by(
                    actor,
                    "transport.stop_removed",
                    target_type="transport_route",
                    target_id=stop.route_id,
                    stop_id=str(stop.id),
                )
            )
            await uow.commit()

    # ---- riders -----------------------------------------------------------------------------

    async def assign(
        self, actor: Principal, student_id: UUID, *, route_id: UUID, stop_id: UUID
    ) -> StudentTransport:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            await _student(uow, tenant_id, student_id)
            await serve_student(uow, actor, student_id, SectionFeature.TRANSPORT)
            route = await _load_route(uow, route_id, for_update=True)  # serialises seat checks
            if not route.active:
                raise InvalidStateTransition("That route is not running.", code="route_inactive")
            stop = await uow.transport.get_stop(stop_id)
            if stop is None or stop.route_id != route.id or not stop.active:
                raise ValidationFailed("Pick a stop on this route.", details={"field": "stop_id"})
            current = await uow.transport.assignment(student_id)
            if current is None or current.route_id != route.id:
                riders = (await uow.transport.rider_counts()).get(route.id, 0)
                if riders >= route.capacity:
                    raise ValidationFailed(
                        f"All {route.capacity} seats on this route are taken.",
                        code="route_full",
                    )
            await uow.transport.save_assignment(
                Assignment(
                    tenant_id=tenant_id,
                    student_id=student_id,
                    route_id=route.id,
                    stop_id=stop.id,
                    by=actor.user_id,
                    at=now,
                )
            )
            uow.audit(
                audit.by(
                    actor,
                    "transport.student_assigned",
                    target_type="school_user",
                    target_id=student_id,
                    route_id=str(route.id),
                    stop_id=str(stop.id),
                    previous_route_id=str(current.route_id) if current else None,
                )
            )
            view = await self._student_view(uow, tenant_id, student_id)
            await uow.commit()
        if view is None:  # pragma: no cover - just assigned
            raise NotFound("Not on a route.")
        return view

    async def unassign(self, actor: Principal, student_id: UUID) -> None:
        actor.require_realm(Realm.SCHOOL)
        actor.require(MANAGE)
        async with self._uow_factory(Scope.tenant(actor.school_tenant_id)) as uow:
            await ensure_student_in_scope(uow, actor, student_id)
            current = await uow.transport.assignment(student_id)
            if current is None:
                raise NotFound("This student isn't on a route.")
            await uow.transport.remove_assignment(student_id)
            uow.audit(
                audit.by(
                    actor,
                    "transport.student_unassigned",
                    target_type="school_user",
                    target_id=student_id,
                    route_id=str(current.route_id),
                )
            )
            await uow.commit()

    async def student_transport(
        self, actor: Principal, student_id: UUID
    ) -> StudentTransport | None:
        """The student's route, stop and today's trips. None when they don't use the bus."""
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            await _student(uow, tenant_id, student_id)
            if not await is_guardian_or_self(uow, actor, student_id):
                _staff_only(actor)
                actor.require(READ)
                await ensure_student_in_scope(uow, actor, student_id)
            return await self._student_view(uow, tenant_id, student_id)

    async def manifest(self, actor: Principal, route_id: UUID, *, direction: str) -> Manifest:
        """Today's riders in stop order, each with their latest status on this trip."""
        actor.require_realm(Realm.SCHOOL)
        _staff_only(actor)
        trip = _direction(direction)
        tenant_id = actor.school_tenant_id
        today = (await self._school_time.now_local(tenant_id)).date()
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            route = await _load_route(uow, route_id)
            _may_see_route(actor, route)
            stops = {s.id: s for s in await uow.transport.stops(route.id)}
            riders = await uow.transport.assignments(route.id)
            names = await uow.users.school_names({a.student_id for a in riders})
            latest = await uow.transport.last_events(route.id, today, trip)
        rows = []
        for a in riders:
            stop = stops.get(a.stop_id)
            event = latest.get(a.student_id)
            rows.append(
                ManifestRow(
                    student_id=a.student_id,
                    student_name=names.get(a.student_id, ""),
                    stop_id=a.stop_id,
                    stop_name=stop.name if stop else "",
                    position=stop.position if stop else 99,
                    status=event.kind.value if event else None,
                    at=event.at if event else None,
                )
            )
        # Morning runs go out along the route; home time runs back from the school.
        rows.sort(
            key=lambda r: (
                r.position if trip is Direction.TO_SCHOOL else -r.position,
                r.student_name,
            )
        )
        return Manifest(
            route_id=route.id,
            route_code=route.code,
            route_name=route.name,
            direction=trip.value,
            day=today,
            riders=rows,
            on_board=sum(1 for r in rows if r.status == BoardingKind.BOARDED.value),
        )

    async def record(
        self, actor: Principal, route_id: UUID, *, student_id: UUID, direction: str, kind: str
    ) -> BoardingView:
        """The route's driver (or the office) records a child getting on or off, or missing."""
        actor.require_realm(Realm.SCHOOL)
        actor.require(BOARD)
        trip = _direction(direction)
        try:
            event_kind = BoardingKind(kind)
        except ValueError as exc:
            raise ValidationFailed("Unknown event.", details={"field": "kind"}) from exc
        tenant_id = actor.school_tenant_id
        now = self._clock.now()
        local = await self._school_time.now_local(tenant_id)
        async with self._uow_factory(Scope.tenant(tenant_id)) as uow:
            route = await _load_route(uow, route_id)
            if route.driver_id != actor.user_id and not actor.permissions.allows(MANAGE):
                raise PermissionDenied("Only this route's driver can record boarding.")
            assignment = await uow.transport.assignment(student_id)
            if assignment is None or assignment.route_id != route.id:
                raise ValidationFailed("This student isn't on this route.", code="not_on_route")
            boarding = Boarding.record(
                tenant_id=tenant_id,
                student_id=student_id,
                route_id=route.id,
                stop_id=assignment.stop_id,
                direction=trip,
                kind=event_kind,
                previous=await uow.transport.last_event(student_id, local.date(), trip),
                day=local.date(),
                by=actor.user_id,
                now=now,
            )
            await uow.transport.add_boarding(boarding)
            stop = await uow.transport.get_stop(assignment.stop_id)
            name = await _student(uow, tenant_id, student_id)
            first = name.split()[0] if name else "Your child"
            title, body = _message(
                first, trip, event_kind, stop.name if stop else "the stop", local.strftime("%H:%M")
            )
            guardians = await uow.guardians.guardians_of(student_id)
            await uow.communication.notify(
                [
                    Notification.new(
                        tenant_id=tenant_id,
                        user_id=g.parent_id,
                        kind=NotificationKind.GENERAL,
                        title=title,
                        body=body,
                        link_type="transport_boarding",
                        link_id=boarding.id,
                        now=now,
                    )
                    for g in guardians
                ]
            )
            uow.audit(
                audit.by(
                    actor,
                    "transport.boarding_recorded",
                    target_type="school_user",
                    target_id=student_id,
                    route_id=str(route.id),
                    direction=trip.value,
                    kind=event_kind.value,
                )
            )
            await uow.commit()
        return _boarding_view(boarding)

    async def history(
        self, actor: Principal, student_id: UUID, *, cursor: str | None, limit: int | None
    ) -> Page[BoardingView]:
        """The last 90 days of trips, newest first."""
        actor.require_realm(Realm.SCHOOL)
        tenant_id = actor.school_tenant_id
        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            await _student(uow, tenant_id, student_id)
            if not await is_guardian_or_self(uow, actor, student_id):
                _staff_only(actor)
                actor.require(READ)
            page = await uow.transport.boardings(
                student_id=student_id,
                since=self._clock.now() - timedelta(days=90),
                cursor=cursor,
                limit=clamp_limit(limit),
            )
        return Page(items=[_boarding_view(b) for b in page.items], next_cursor=page.next_cursor)

    # ---- helpers ----------------------------------------------------------------------------

    async def _student_view(
        self, uow: UnitOfWork, tenant_id: UUID, student_id: UUID
    ) -> StudentTransport | None:
        a = await uow.transport.assignment(student_id)
        if a is None:
            return None
        route = await uow.transport.get_route(a.route_id)
        stop = await uow.transport.get_stop(a.stop_id)
        if route is None or stop is None:  # pragma: no cover - foreign keys
            return None
        ids = {student_id} | ({route.driver_id} if route.driver_id else set())
        names = await uow.users.school_names(ids)
        today = (await self._school_time.now_local(tenant_id)).date()
        trips: dict[Direction, TripStatus | None] = {}
        for trip in Direction:
            latest = (await uow.transport.last_events(route.id, today, trip)).get(student_id)
            trips[trip] = TripStatus(kind=latest.kind.value, at=latest.at) if latest else None
        return StudentTransport(
            student_id=student_id,
            student_name=names.get(student_id, ""),
            route_id=route.id,
            route_name=route.name,
            route_code=route.code,
            vehicle_plate=route.vehicle_plate,
            driver_name=names.get(route.driver_id) if route.driver_id else None,
            stop=_stop_view(stop),
            to_school=trips[Direction.TO_SCHOOL],
            from_school=trips[Direction.FROM_SCHOOL],
        )


def _message(
    first: str, trip: Direction, kind: BoardingKind, stop: str, at: str
) -> tuple[str, str]:
    if trip is Direction.TO_SCHOOL:
        return {
            BoardingKind.BOARDED: (f"{first} is on the bus to school", f"Boarded at {stop}, {at}."),
            BoardingKind.DROPPED: (f"{first} has arrived at school", f"Off the bus at {at}."),
            BoardingKind.ABSENT: (
                f"{first} wasn't at the bus stop",
                f"The bus left {stop} at {at} without {first}. Call the school if this is "
                "unexpected.",
            ),
        }[kind]
    return {
        BoardingKind.BOARDED: (f"{first} is on the bus home", f"Left school at {at}."),
        BoardingKind.DROPPED: (f"{first} has been dropped off", f"At {stop}, {at}."),
        BoardingKind.ABSENT: (
            f"{first} didn't take the bus home",
            "Call the school if this is unexpected.",
        ),
    }[kind]


def _direction(value: str) -> Direction:
    try:
        return Direction(value)
    except ValueError as exc:
        raise ValidationFailed("Unknown direction.", details={"field": "direction"}) from exc


def _staff_only(actor: Principal) -> None:
    if not actor.roles - _FAMILY:
        raise PermissionDenied("For staff.")


def _may_see_route(actor: Principal, route: Route) -> None:
    if route.driver_id == actor.user_id or actor.permissions.allows(READ):
        return
    raise PermissionDenied("You can see only the routes you drive.")


async def _check_driver(uow: UnitOfWork, tenant_id: UUID, driver_id: UUID | None) -> None:
    if driver_id is None:
        return
    user = await uow.users.get(driver_id)
    if user is None or user.tenant_id != tenant_id or SchoolRole.DRIVER.value not in user.roles:
        raise ValidationFailed(
            "Pick a member of staff with the driver role.", details={"field": "driver_id"}
        )


async def _load_route(uow: UnitOfWork, route_id: UUID, *, for_update: bool = False) -> Route:
    route = await uow.transport.get_route(route_id, for_update=for_update)
    if route is None:
        raise NotFound("Route not found.")
    return route


async def _load_stop(uow: UnitOfWork, stop_id: UUID) -> Stop:
    stop = await uow.transport.get_stop(stop_id)
    if stop is None or not stop.active:
        raise NotFound("Stop not found.")
    return stop


async def _student(uow: UnitOfWork, tenant_id: UUID, student_id: UUID) -> str:
    user = await uow.users.get(student_id)
    if user is None or user.tenant_id != tenant_id or _STUDENT not in user.roles:
        raise NotFound("Student not found.")
    return user.full_name


async def _route_view(uow: UnitOfWork, route: Route, *, riders: int) -> RouteView:
    stops = await uow.transport.stops(route.id)
    driver = (
        (await uow.users.school_names({route.driver_id})).get(route.driver_id)
        if route.driver_id
        else None
    )
    return RouteView(
        id=route.id,
        name=route.name,
        code=route.code,
        vehicle_plate=route.vehicle_plate,
        capacity=route.capacity,
        riders=riders,
        driver_id=route.driver_id,
        driver_name=driver,
        active=route.active,
        stops=[_stop_view(s) for s in stops],
    )


def _stop_view(s: Stop) -> StopView:
    return StopView(
        id=s.id,
        name=s.name,
        position=s.position,
        morning_at=s.morning_at,
        afternoon_at=s.afternoon_at,
        landmark=s.landmark,
    )


def _boarding_view(b: Boarding) -> BoardingView:
    return BoardingView(
        id=b.id,
        student_id=b.student_id,
        route_id=b.route_id,
        stop_id=b.stop_id,
        direction=b.direction.value,
        kind=b.kind.value,
        day=b.day,
        at=b.at,
    )
