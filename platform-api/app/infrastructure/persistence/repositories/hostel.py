"""Hostels, rooms, allocations and exeats."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.operations.hostel import (
    OPEN_EXEAT,
    Allocation,
    Exeat,
    ExeatStatus,
    Hostel,
    HostelKind,
    Room,
)
from app.domain.shared.value_objects import Page
from app.infrastructure.persistence.models.hostel import (
    AllocationRow,
    ExeatRow,
    HostelRow,
    RoomRow,
)
from app.infrastructure.persistence.repositories.finance import _decode, _encode
from app.infrastructure.persistence.repositories.scope_state import ScopeState


def _hostel(r: HostelRow) -> Hostel:
    return Hostel(
        id=r.id,
        tenant_id=r.tenant_id,
        name=r.name,
        kind=HostelKind(r.kind),
        warden_id=r.warden_id,
        active=r.active,
        created_at=r.created_at,
    )


def _room(r: RoomRow) -> Room:
    return Room(
        id=r.id,
        tenant_id=r.tenant_id,
        hostel_id=r.hostel_id,
        name=r.name,
        capacity=r.capacity,
        active=r.active,
    )


def _allocation(r: AllocationRow) -> Allocation:
    return Allocation(
        tenant_id=r.tenant_id,
        student_id=r.student_id,
        room_id=r.room_id,
        bed=r.bed,
        by=r.by,
        at=r.at,
    )


def _exeat(r: ExeatRow) -> Exeat:
    return Exeat(
        id=r.id,
        tenant_id=r.tenant_id,
        student_id=r.student_id,
        requested_by=r.requested_by,
        reason=r.reason,
        collector_name=r.collector_name,
        leave_at=r.leave_at,
        return_by=r.return_by,
        status=ExeatStatus(r.status),
        created_at=r.created_at,
        decided_by=r.decided_by,
        decided_at=r.decided_at,
        decision_note=r.decision_note,
        left_at=r.left_at,
        returned_at=r.returned_at,
    )


class SqlHostelRepository:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    @property
    def _tid(self) -> UUID:
        if self._scope.tenant_id is None:
            raise RuntimeError("Hostels need a tenant scope.")
        return self._scope.tenant_id

    # ---- hostels and rooms ------------------------------------------------------------------

    async def add_hostel(self, hostel: Hostel) -> None:
        await self._s.execute(
            pg_insert(HostelRow).values(
                tenant_id=self._tid,
                id=hostel.id,
                name=hostel.name,
                kind=hostel.kind.value,
                warden_id=hostel.warden_id,
                active=hostel.active,
                created_at=hostel.created_at,
            )
        )

    async def get_hostel(self, hostel_id: UUID) -> Hostel | None:
        row = await self._s.scalar(
            select(HostelRow).where(HostelRow.tenant_id == self._tid, HostelRow.id == hostel_id)
        )
        return _hostel(row) if row else None

    async def save_hostel(self, hostel: Hostel) -> None:
        await self._s.execute(
            update(HostelRow)
            .where(HostelRow.tenant_id == self._tid, HostelRow.id == hostel.id)
            .values(name=hostel.name, warden_id=hostel.warden_id, active=hostel.active)
        )

    async def hostels(self, *, include_inactive: bool) -> list[Hostel]:
        stmt = select(HostelRow).where(HostelRow.tenant_id == self._tid)
        if not include_inactive:
            stmt = stmt.where(HostelRow.active.is_(True))
        return [_hostel(r) for r in await self._s.scalars(stmt.order_by(HostelRow.name))]

    async def add_room(self, room: Room) -> None:
        await self._s.execute(
            pg_insert(RoomRow).values(
                tenant_id=self._tid,
                id=room.id,
                hostel_id=room.hostel_id,
                name=room.name,
                capacity=room.capacity,
                active=room.active,
            )
        )

    async def get_room(self, room_id: UUID, *, for_update: bool = False) -> Room | None:
        stmt = select(RoomRow).where(RoomRow.tenant_id == self._tid, RoomRow.id == room_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _room(row) if row else None

    async def save_room(self, room: Room) -> None:
        await self._s.execute(
            update(RoomRow)
            .where(RoomRow.tenant_id == self._tid, RoomRow.id == room.id)
            .values(name=room.name, capacity=room.capacity, active=room.active)
        )

    async def rooms(self, hostel_id: UUID) -> list[Room]:
        rows = await self._s.scalars(
            select(RoomRow)
            .where(
                RoomRow.tenant_id == self._tid,
                RoomRow.hostel_id == hostel_id,
                RoomRow.active.is_(True),
            )
            .order_by(RoomRow.name)
        )
        return [_room(r) for r in rows]

    async def occupancy(self) -> dict[UUID, int]:
        rows = await self._s.execute(
            select(AllocationRow.room_id, func.count())
            .where(AllocationRow.tenant_id == self._tid)
            .group_by(AllocationRow.room_id)
        )
        return {row[0]: int(row[1]) for row in rows.all()}

    # ---- allocations ------------------------------------------------------------------------

    async def allocation(self, student_id: UUID) -> Allocation | None:
        row = await self._s.scalar(
            select(AllocationRow).where(
                AllocationRow.tenant_id == self._tid, AllocationRow.student_id == student_id
            )
        )
        return _allocation(row) if row else None

    async def bed_holder(self, room_id: UUID, bed: str) -> UUID | None:
        holder: UUID | None = await self._s.scalar(
            select(AllocationRow.student_id).where(
                AllocationRow.tenant_id == self._tid,
                AllocationRow.room_id == room_id,
                AllocationRow.bed == bed,
            )
        )
        return holder

    async def save_allocation(self, allocation: Allocation) -> None:
        a = allocation
        stmt = pg_insert(AllocationRow).values(
            tenant_id=self._tid,
            student_id=a.student_id,
            room_id=a.room_id,
            bed=a.bed,
            by=a.by,
            at=a.at,
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[AllocationRow.tenant_id, AllocationRow.student_id],
                set_={
                    "room_id": stmt.excluded.room_id,
                    "bed": stmt.excluded.bed,
                    "by": stmt.excluded.by,
                    "at": stmt.excluded.at,
                },
            )
        )

    async def remove_allocation(self, student_id: UUID) -> None:
        await self._s.execute(
            delete(AllocationRow).where(
                AllocationRow.tenant_id == self._tid, AllocationRow.student_id == student_id
            )
        )

    async def allocations(self, hostel_id: UUID) -> list[Allocation]:
        rows = await self._s.scalars(
            select(AllocationRow)
            .join(
                RoomRow,
                (RoomRow.tenant_id == AllocationRow.tenant_id)
                & (RoomRow.id == AllocationRow.room_id),
            )
            .where(AllocationRow.tenant_id == self._tid, RoomRow.hostel_id == hostel_id)
        )
        return [_allocation(r) for r in rows]

    # ---- exeats -----------------------------------------------------------------------------

    async def add_exeat(self, exeat: Exeat) -> None:
        e = exeat
        await self._s.execute(
            pg_insert(ExeatRow).values(
                tenant_id=self._tid,
                id=e.id,
                student_id=e.student_id,
                requested_by=e.requested_by,
                reason=e.reason,
                collector_name=e.collector_name,
                leave_at=e.leave_at,
                return_by=e.return_by,
                status=e.status.value,
                created_at=e.created_at,
            )
        )

    async def get_exeat(self, exeat_id: UUID, *, for_update: bool = False) -> Exeat | None:
        stmt = select(ExeatRow).where(ExeatRow.tenant_id == self._tid, ExeatRow.id == exeat_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _exeat(row) if row else None

    async def save_exeat(self, exeat: Exeat) -> None:
        e = exeat
        await self._s.execute(
            update(ExeatRow)
            .where(ExeatRow.tenant_id == self._tid, ExeatRow.id == e.id)
            .values(
                status=e.status.value,
                decided_by=e.decided_by,
                decided_at=e.decided_at,
                decision_note=e.decision_note,
                left_at=e.left_at,
                returned_at=e.returned_at,
            )
        )

    async def students_out(self) -> set[UUID]:
        rows = await self._s.scalars(
            select(ExeatRow.student_id).where(
                ExeatRow.tenant_id == self._tid, ExeatRow.status == ExeatStatus.OUT.value
            )
        )
        return set(rows)

    async def open_exeat(self, student_id: UUID) -> Exeat | None:
        row = await self._s.scalar(
            select(ExeatRow).where(
                ExeatRow.tenant_id == self._tid,
                ExeatRow.student_id == student_id,
                ExeatRow.status.in_([s.value for s in OPEN_EXEAT]),
            )
        )
        return _exeat(row) if row else None

    async def exeats(
        self,
        *,
        student_id: UUID | None,
        statuses: frozenset[ExeatStatus] | None,
        overdue_at: datetime | None,
        cursor: str | None,
        limit: int,
    ) -> Page[Exeat]:
        stmt = select(ExeatRow).where(ExeatRow.tenant_id == self._tid)
        if student_id is not None:
            stmt = stmt.where(ExeatRow.student_id == student_id)
        if statuses:
            stmt = stmt.where(ExeatRow.status.in_([s.value for s in statuses]))
        if overdue_at is not None:
            stmt = stmt.where(
                ExeatRow.status == ExeatStatus.OUT.value, ExeatRow.return_by < overdue_at
            )
        if cursor:
            stmt = stmt.where(ExeatRow.id < _decode(cursor))
        rows = list(await self._s.scalars(stmt.order_by(ExeatRow.id.desc()).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_exeat(r) for r in rows],
            next_cursor=_encode(rows[-1].id) if more and rows else None,
        )
