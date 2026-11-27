"""Clinic records and visits; the gate's collectors, releases and visitors."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.operations.clinic import Outcome
from app.domain.operations.gate import Collector, Release, ReleaseStatus, Visit
from app.domain.operations.repositories import StoredHealthRecord, StoredVisit
from app.domain.shared.value_objects import Page
from app.infrastructure.persistence.models.operations import (
    ClinicVisitRow,
    CollectorRow,
    HealthRecordRow,
    ReleaseRow,
    VisitorRow,
)
from app.infrastructure.persistence.repositories.finance import _decode, _encode
from app.infrastructure.persistence.repositories.scope_state import ScopeState


class _Scoped:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    @property
    def _tid(self) -> UUID:
        if self._scope.tenant_id is None:
            raise RuntimeError("Operations need a tenant scope.")
        return self._scope.tenant_id


def _visit(r: ClinicVisitRow) -> StoredVisit:
    return StoredVisit(
        id=r.id,
        student_id=r.student_id,
        arrived_at=r.arrived_at,
        temperature_dc=r.temperature_dc,
        outcome=Outcome(r.outcome) if r.outcome else None,
        left_at=r.left_at,
        recorded_by=r.recorded_by,
        notes_ciphertext=r.notes_ciphertext,
    )


class SqlClinicRepository(_Scoped):
    async def get_record(self, student_id: UUID) -> StoredHealthRecord | None:
        r = await self._s.scalar(
            select(HealthRecordRow).where(
                HealthRecordRow.tenant_id == self._tid, HealthRecordRow.student_id == student_id
            )
        )
        if r is None:
            return None
        return StoredHealthRecord(
            student_id=r.student_id,
            blood_group=r.blood_group,
            genotype=r.genotype,
            details_ciphertext=r.details_ciphertext,
            alert_ciphertext=r.alert_ciphertext,
            updated_by=r.updated_by,
            updated_at=r.updated_at,
        )

    async def save_record(self, record: StoredHealthRecord) -> None:
        values: dict[str, Any] = {
            "blood_group": record.blood_group,
            "genotype": record.genotype,
            "details_ciphertext": record.details_ciphertext,
            "alert_ciphertext": record.alert_ciphertext,
            "updated_by": record.updated_by,
            "updated_at": record.updated_at,
        }
        stmt = pg_insert(HealthRecordRow).values(
            tenant_id=self._tid, student_id=record.student_id, **values
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[HealthRecordRow.tenant_id, HealthRecordRow.student_id], set_=values
            )
        )

    async def add_visit(self, visit: StoredVisit) -> None:
        await self._s.execute(
            pg_insert(ClinicVisitRow).values(
                tenant_id=self._tid,
                id=visit.id,
                student_id=visit.student_id,
                recorded_by=visit.recorded_by,
                arrived_at=visit.arrived_at,
                **self._visit_values(visit),
            )
        )

    async def get_visit(self, visit_id: UUID, *, for_update: bool = False) -> StoredVisit | None:
        stmt = select(ClinicVisitRow).where(
            ClinicVisitRow.tenant_id == self._tid, ClinicVisitRow.id == visit_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _visit(row) if row else None

    async def save_visit(self, visit: StoredVisit) -> None:
        await self._s.execute(
            update(ClinicVisitRow)
            .where(ClinicVisitRow.tenant_id == self._tid, ClinicVisitRow.id == visit.id)
            .values(**self._visit_values(visit))
        )

    async def visits(
        self,
        *,
        student_id: UUID | None,
        open_only: bool,
        since: datetime | None,
        cursor: str | None,
        limit: int,
    ) -> Page[StoredVisit]:
        v = ClinicVisitRow
        stmt = select(v).where(v.tenant_id == self._tid)
        if student_id is not None:
            stmt = stmt.where(v.student_id == student_id)
        if open_only:
            stmt = stmt.where(v.outcome.is_(None))
        if since is not None:
            stmt = stmt.where(v.arrived_at >= since)
        if cursor:
            stmt = stmt.where(v.id < _decode(cursor))
        rows = list(await self._s.scalars(stmt.order_by(v.id.desc()).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_visit(r) for r in rows],
            next_cursor=_encode(rows[-1].id) if more and rows else None,
        )

    @staticmethod
    def _visit_values(visit: StoredVisit) -> dict[str, Any]:
        return {
            "temperature_dc": visit.temperature_dc,
            "outcome": visit.outcome.value if visit.outcome else None,
            "left_at": visit.left_at,
            "notes_ciphertext": visit.notes_ciphertext,
        }


def _collector(r: CollectorRow) -> Collector:
    return Collector(
        id=r.id,
        tenant_id=r.tenant_id,
        student_id=r.student_id,
        name=r.name,
        relationship=r.relationship,
        phone=r.phone,
        id_document=r.id_document,
        valid_on=r.valid_on,
        active=r.active,
        added_by=r.added_by,
        added_at=r.added_at,
    )


def _release(r: ReleaseRow) -> Release:
    return Release(
        id=r.id,
        tenant_id=r.tenant_id,
        student_id=r.student_id,
        status=ReleaseStatus(r.status),
        guardian_id=r.guardian_id,
        collector_id=r.collector_id,
        collector_name=r.collector_name,
        note=r.note,
        by=r.by,
        at=r.at,
    )


def _gate_visit(r: VisitorRow) -> Visit:
    return Visit(
        id=r.id,
        tenant_id=r.tenant_id,
        name=r.name,
        phone=r.phone,
        organisation=r.organisation,
        purpose=r.purpose,
        host_id=r.host_id,
        badge=r.badge,
        vehicle=r.vehicle,
        signed_in_at=r.signed_in_at,
        signed_in_by=r.signed_in_by,
        signed_out_at=r.signed_out_at,
        signed_out_by=r.signed_out_by,
    )


class SqlGateRepository(_Scoped):
    async def add_collector(self, collector: Collector) -> None:
        c = collector
        await self._s.execute(
            pg_insert(CollectorRow).values(
                tenant_id=self._tid,
                id=c.id,
                student_id=c.student_id,
                name=c.name,
                relationship=c.relationship,
                phone=c.phone,
                id_document=c.id_document,
                valid_on=c.valid_on,
                active=c.active,
                added_by=c.added_by,
                added_at=c.added_at,
            )
        )

    async def get_collector(self, collector_id: UUID) -> Collector | None:
        row = await self._s.scalar(
            select(CollectorRow).where(
                CollectorRow.tenant_id == self._tid, CollectorRow.id == collector_id
            )
        )
        return _collector(row) if row else None

    async def save_collector(self, collector: Collector) -> None:
        await self._s.execute(
            update(CollectorRow)
            .where(CollectorRow.tenant_id == self._tid, CollectorRow.id == collector.id)
            .values(active=collector.active)
        )

    async def collectors_of(self, student_id: UUID, *, include_inactive: bool) -> list[Collector]:
        stmt = select(CollectorRow).where(
            CollectorRow.tenant_id == self._tid, CollectorRow.student_id == student_id
        )
        if not include_inactive:
            stmt = stmt.where(CollectorRow.active.is_(True))
        return [_collector(r) for r in await self._s.scalars(stmt.order_by(CollectorRow.name))]

    async def add_release(self, release: Release) -> None:
        r = release
        await self._s.execute(
            pg_insert(ReleaseRow).values(
                tenant_id=self._tid,
                id=r.id,
                student_id=r.student_id,
                status=r.status.value,
                guardian_id=r.guardian_id,
                collector_id=r.collector_id,
                collector_name=r.collector_name,
                note=r.note,
                by=r.by,
                at=r.at,
            )
        )

    async def releases(
        self, *, student_id: UUID | None, since: datetime | None, cursor: str | None, limit: int
    ) -> Page[Release]:
        stmt = select(ReleaseRow).where(ReleaseRow.tenant_id == self._tid)
        if student_id is not None:
            stmt = stmt.where(ReleaseRow.student_id == student_id)
        if since is not None:
            stmt = stmt.where(ReleaseRow.at >= since)
        if cursor:
            stmt = stmt.where(ReleaseRow.id < _decode(cursor))
        rows = list(await self._s.scalars(stmt.order_by(ReleaseRow.id.desc()).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_release(r) for r in rows],
            next_cursor=_encode(rows[-1].id) if more and rows else None,
        )

    async def add_visit(self, visit: Visit) -> None:
        v = visit
        await self._s.execute(
            pg_insert(VisitorRow).values(
                tenant_id=self._tid,
                id=v.id,
                name=v.name,
                phone=v.phone,
                organisation=v.organisation,
                purpose=v.purpose,
                host_id=v.host_id,
                badge=v.badge,
                vehicle=v.vehicle,
                signed_in_at=v.signed_in_at,
                signed_in_by=v.signed_in_by,
            )
        )

    async def get_visit(self, visit_id: UUID, *, for_update: bool = False) -> Visit | None:
        stmt = select(VisitorRow).where(
            VisitorRow.tenant_id == self._tid, VisitorRow.id == visit_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return _gate_visit(row) if row else None

    async def save_visit(self, visit: Visit) -> None:
        await self._s.execute(
            update(VisitorRow)
            .where(VisitorRow.tenant_id == self._tid, VisitorRow.id == visit.id)
            .values(signed_out_at=visit.signed_out_at, signed_out_by=visit.signed_out_by)
        )

    async def visits(
        self, *, on_site_only: bool, since: datetime | None, cursor: str | None, limit: int
    ) -> Page[Visit]:
        stmt = select(VisitorRow).where(VisitorRow.tenant_id == self._tid)
        if on_site_only:
            stmt = stmt.where(VisitorRow.signed_out_at.is_(None))
        if since is not None:
            stmt = stmt.where(VisitorRow.signed_in_at >= since)
        if cursor:
            stmt = stmt.where(VisitorRow.id < _decode(cursor))
        rows = list(await self._s.scalars(stmt.order_by(VisitorRow.id.desc()).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_gate_visit(r) for r in rows],
            next_cursor=_encode(rows[-1].id) if more and rows else None,
        )
