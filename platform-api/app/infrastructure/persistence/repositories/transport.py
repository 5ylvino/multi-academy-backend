"""Transport routes, stops, assignments and boarding events."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.operations.transport import (
    Assignment,
    Boarding,
    BoardingKind,
    Direction,
    Route,
    Stop,
)
from app.domain.shared.value_objects import Page
from app.infrastructure.persistence.models.transport import (
    AssignmentRow,
    BoardingRow,
    RouteRow,
    StopRow,
)
from app.infrastructure.persistence.repositories.finance import _decode, _encode
from app.infrastructure.persistence.repositories.scope_state import ScopeState


def _route(r: RouteRow) -> Route:
    return Route(
        id=r.id,
        tenant_id=r.tenant_id,
        name=r.name,
        code=r.code,
        vehicle_plate=r.vehicle_plate,
        capacity=r.capacity,
        driver_id=r.driver_id,
        active=r.active,
        created_at=r.created_at,
    )


def _stop(r: StopRow) -> Stop:
    return Stop(
        id=r.id,
        tenant_id=r.tenant_id,
        route_id=r.route_id,
        name=r.name,
        position=r.position,
        morning_at=r.morning_at,
        afternoon_at=r.afternoon_at,
        landmark=r.landmark,
        active=r.active,
    )


def _assignment(r: AssignmentRow) -> Assignment:
    return Assignment(
        tenant_id=r.tenant_id,
        student_id=r.student_id,
        route_id=r.route_id,
        stop_id=r.stop_id,
        by=r.by,
        at=r.at,
    )


def _boarding(r: BoardingRow) -> Boarding:
    return Boarding(
        id=r.id,
        tenant_id=r.tenant_id,
        student_id=r.student_id,
        route_id=r.route_id,
        stop_id=r.stop_id,
        direction=Direction(r.direction),
        kind=BoardingKind(r.kind),
        day=r.day,
        by=r.by,
        at=r.at,
    )


class SqlTransportRepository:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    @property
    def _tid(self) -> UUID:
        if self._scope.tenant_id is None:
            raise RuntimeError("Transport needs a tenant scope.")
        return self._scope.tenant_id

    # ---- routes -----------------------------------------------------------------------------

    async def add_route(self, route: Route) -> None:
        await self._s.execute(
            pg_insert(RouteRow).values(
                tenant_id=self._tid,
                id=route.id,
                name=route.name,
                code=route.code,
                vehicle_plate=route.vehicle_plate,
                capacity=route.capacity,
                driver_id=route.driver_id,
                active=route.active,
                created_at=route.created_at,
            )
        )

    async def get_route(self, route_id: UUID, *, for_update: bool = False) -> Route | None:
        stmt = select(RouteRow).where(RouteRow.tenant_id == self._tid, RouteRow.id == route_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _route(row) if row else None

    async def route_by_code(self, code: str) -> Route | None:
        row = await self._s.scalar(
            select(RouteRow).where(RouteRow.tenant_id == self._tid, RouteRow.code == code)
        )
        return _route(row) if row else None

    async def save_route(self, route: Route) -> None:
        await self._s.execute(
            update(RouteRow)
            .where(RouteRow.tenant_id == self._tid, RouteRow.id == route.id)
            .values(
                name=route.name,
                vehicle_plate=route.vehicle_plate,
                capacity=route.capacity,
                driver_id=route.driver_id,
                active=route.active,
            )
        )

    async def routes(self, *, include_inactive: bool) -> list[Route]:
        stmt = select(RouteRow).where(RouteRow.tenant_id == self._tid)
        if not include_inactive:
            stmt = stmt.where(RouteRow.active.is_(True))
        return [_route(r) for r in await self._s.scalars(stmt.order_by(RouteRow.code))]

    async def routes_driven_by(self, driver_id: UUID) -> list[Route]:
        rows = await self._s.scalars(
            select(RouteRow)
            .where(
                RouteRow.tenant_id == self._tid,
                RouteRow.driver_id == driver_id,
                RouteRow.active.is_(True),
            )
            .order_by(RouteRow.code)
        )
        return [_route(r) for r in rows]

    async def rider_counts(self) -> dict[UUID, int]:
        rows = await self._s.execute(
            select(AssignmentRow.route_id, func.count())
            .where(AssignmentRow.tenant_id == self._tid)
            .group_by(AssignmentRow.route_id)
        )
        return {row[0]: int(row[1]) for row in rows.all()}

    # ---- stops ------------------------------------------------------------------------------

    async def add_stop(self, stop: Stop) -> None:
        await self._s.execute(
            pg_insert(StopRow).values(
                tenant_id=self._tid,
                id=stop.id,
                route_id=stop.route_id,
                name=stop.name,
                position=stop.position,
                morning_at=stop.morning_at,
                afternoon_at=stop.afternoon_at,
                landmark=stop.landmark,
                active=stop.active,
            )
        )

    async def get_stop(self, stop_id: UUID) -> Stop | None:
        row = await self._s.scalar(
            select(StopRow).where(StopRow.tenant_id == self._tid, StopRow.id == stop_id)
        )
        return _stop(row) if row else None

    async def save_stop(self, stop: Stop) -> None:
        await self._s.execute(
            update(StopRow)
            .where(StopRow.tenant_id == self._tid, StopRow.id == stop.id)
            .values(
                name=stop.name,
                position=stop.position,
                morning_at=stop.morning_at,
                afternoon_at=stop.afternoon_at,
                landmark=stop.landmark,
                active=stop.active,
            )
        )

    async def stops(self, route_id: UUID) -> list[Stop]:
        rows = await self._s.scalars(
            select(StopRow)
            .where(
                StopRow.tenant_id == self._tid,
                StopRow.route_id == route_id,
                StopRow.active.is_(True),
            )
            .order_by(StopRow.position, StopRow.name)
        )
        return [_stop(r) for r in rows]

    async def stop_in_use(self, stop_id: UUID) -> bool:
        used = await self._s.scalar(
            select(
                exists().where(
                    AssignmentRow.tenant_id == self._tid, AssignmentRow.stop_id == stop_id
                )
            )
        )
        return bool(used)

    # ---- assignments ------------------------------------------------------------------------

    async def assignment(self, student_id: UUID) -> Assignment | None:
        row = await self._s.scalar(
            select(AssignmentRow).where(
                AssignmentRow.tenant_id == self._tid, AssignmentRow.student_id == student_id
            )
        )
        return _assignment(row) if row else None

    async def save_assignment(self, assignment: Assignment) -> None:
        a = assignment
        stmt = pg_insert(AssignmentRow).values(
            tenant_id=self._tid,
            student_id=a.student_id,
            route_id=a.route_id,
            stop_id=a.stop_id,
            by=a.by,
            at=a.at,
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[AssignmentRow.tenant_id, AssignmentRow.student_id],
                set_={
                    "route_id": stmt.excluded.route_id,
                    "stop_id": stmt.excluded.stop_id,
                    "by": stmt.excluded.by,
                    "at": stmt.excluded.at,
                },
            )
        )

    async def remove_assignment(self, student_id: UUID) -> None:
        await self._s.execute(
            delete(AssignmentRow).where(
                AssignmentRow.tenant_id == self._tid, AssignmentRow.student_id == student_id
            )
        )

    async def assignments(self, route_id: UUID) -> list[Assignment]:
        rows = await self._s.scalars(
            select(AssignmentRow).where(
                AssignmentRow.tenant_id == self._tid, AssignmentRow.route_id == route_id
            )
        )
        return [_assignment(r) for r in rows]

    # ---- boarding ---------------------------------------------------------------------------

    async def add_boarding(self, boarding: Boarding) -> None:
        b = boarding
        await self._s.execute(
            pg_insert(BoardingRow).values(
                tenant_id=self._tid,
                id=b.id,
                student_id=b.student_id,
                route_id=b.route_id,
                stop_id=b.stop_id,
                direction=b.direction.value,
                kind=b.kind.value,
                day=b.day,
                by=b.by,
                at=b.at,
            )
        )

    async def last_events(
        self, route_id: UUID, day: date, direction: Direction
    ) -> dict[UUID, Boarding]:
        rows = await self._s.scalars(
            select(BoardingRow)
            .ext(distinct_on(BoardingRow.student_id))
            .where(
                BoardingRow.tenant_id == self._tid,
                BoardingRow.route_id == route_id,
                BoardingRow.day == day,
                BoardingRow.direction == direction.value,
            )
            .order_by(BoardingRow.student_id, BoardingRow.at.desc(), BoardingRow.id.desc())
        )
        return {r.student_id: _boarding(r) for r in rows}

    async def last_event(
        self, student_id: UUID, day: date, direction: Direction
    ) -> BoardingKind | None:
        kind = await self._s.scalar(
            select(BoardingRow.kind)
            .where(
                BoardingRow.tenant_id == self._tid,
                BoardingRow.student_id == student_id,
                BoardingRow.day == day,
                BoardingRow.direction == direction.value,
            )
            .order_by(BoardingRow.at.desc(), BoardingRow.id.desc())
            .limit(1)
        )
        return BoardingKind(kind) if kind else None

    async def boardings(
        self, *, student_id: UUID, since: datetime, cursor: str | None, limit: int
    ) -> Page[Boarding]:
        stmt = select(BoardingRow).where(
            BoardingRow.tenant_id == self._tid,
            BoardingRow.student_id == student_id,
            BoardingRow.at >= since,
        )
        if cursor:
            stmt = stmt.where(BoardingRow.id < _decode(cursor))
        rows = list(await self._s.scalars(stmt.order_by(BoardingRow.id.desc()).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_boarding(r) for r in rows],
            next_cursor=_encode(rows[-1].id) if more and rows else None,
        )
