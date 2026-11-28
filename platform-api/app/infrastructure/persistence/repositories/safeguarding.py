"""Safeguarding concerns and their chronology. Text arrives and leaves encrypted."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.safeguarding.concerns import (
    CaseStatus,
    Category,
    Concern,
    Entry,
    EntryKind,
    Severity,
)
from app.domain.shared.value_objects import Page
from app.infrastructure.persistence.models.safeguarding import ConcernRow, EntryRow
from app.infrastructure.persistence.repositories.finance import _decode, _encode
from app.infrastructure.persistence.repositories.scope_state import ScopeState


def _concern(r: ConcernRow) -> Concern:
    return Concern(
        id=r.id,
        tenant_id=r.tenant_id,
        student_id=r.student_id,
        category=Category(r.category),
        severity=Severity(r.severity),
        summary="",
        observed_at=r.observed_at,
        reported_by=r.reported_by,
        reported_at=r.reported_at,
        status=CaseStatus(r.status),
        lead_id=r.lead_id,
        closed_at=r.closed_at,
    )


class SqlSafeguardingRepository:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    @property
    def _tid(self) -> UUID:
        if self._scope.tenant_id is None:
            raise RuntimeError("Safeguarding needs a tenant scope.")
        return self._scope.tenant_id

    async def add(self, concern: Concern, *, summary_ciphertext: str) -> None:
        c = concern
        await self._s.execute(
            pg_insert(ConcernRow).values(
                tenant_id=self._tid,
                id=c.id,
                student_id=c.student_id,
                category=c.category.value,
                severity=c.severity.value,
                summary_ciphertext=summary_ciphertext,
                observed_at=c.observed_at,
                reported_by=c.reported_by,
                reported_at=c.reported_at,
                status=c.status.value,
                lead_id=c.lead_id,
                closed_at=c.closed_at,
            )
        )

    async def get(
        self, concern_id: UUID, *, for_update: bool = False
    ) -> tuple[Concern, str] | None:
        stmt = select(ConcernRow).where(
            ConcernRow.tenant_id == self._tid, ConcernRow.id == concern_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._s.scalar(stmt)
        return (_concern(row), row.summary_ciphertext) if row else None

    async def save(self, concern: Concern, *, entry_ciphertexts: dict[UUID, str]) -> None:
        c = concern
        await self._s.execute(
            update(ConcernRow)
            .where(ConcernRow.tenant_id == self._tid, ConcernRow.id == c.id)
            .values(status=c.status.value, lead_id=c.lead_id, closed_at=c.closed_at)
        )
        if c.new_entries:
            await self._s.execute(
                pg_insert(EntryRow).values(
                    [
                        {
                            "tenant_id": self._tid,
                            "id": e.id,
                            "concern_id": c.id,
                            "kind": e.kind.value,
                            "text_ciphertext": entry_ciphertexts[e.id],
                            "by": e.by,
                            "at": e.at,
                            "agency": e.agency,
                            "agency_reference": e.agency_reference,
                            "status": e.status.value if e.status else None,
                        }
                        for e in c.new_entries
                    ]
                )
            )
            c.new_entries.clear()

    async def entries(self, concern_id: UUID) -> list[tuple[Entry, str]]:
        rows = await self._s.scalars(
            select(EntryRow)
            .where(EntryRow.tenant_id == self._tid, EntryRow.concern_id == concern_id)
            .order_by(EntryRow.id)
        )
        return [
            (
                Entry(
                    id=r.id,
                    concern_id=r.concern_id,
                    kind=EntryKind(r.kind),
                    text="",
                    by=r.by,
                    at=r.at,
                    agency=r.agency,
                    agency_reference=r.agency_reference,
                    status=CaseStatus(r.status) if r.status else None,
                ),
                r.text_ciphertext,
            )
            for r in rows
        ]

    async def search(
        self,
        *,
        status: str | None,
        student_id: UUID | None,
        reported_by: UUID | None,
        cursor: str | None,
        limit: int,
    ) -> Page[Concern]:
        stmt = select(ConcernRow).where(ConcernRow.tenant_id == self._tid)
        if status is not None:
            stmt = stmt.where(ConcernRow.status == status)
        if student_id is not None:
            stmt = stmt.where(ConcernRow.student_id == student_id)
        if reported_by is not None:
            stmt = stmt.where(ConcernRow.reported_by == reported_by)
        if cursor:
            stmt = stmt.where(ConcernRow.id < _decode(cursor))
        rows = list(await self._s.scalars(stmt.order_by(ConcernRow.id.desc()).limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        return Page(
            items=[_concern(r) for r in rows],
            next_cursor=_encode(rows[-1].id) if more and rows else None,
        )

    async def counts(self) -> dict[str, int]:
        rows = await self._s.execute(
            select(ConcernRow.status, func.count())
            .where(ConcernRow.tenant_id == self._tid)
            .group_by(ConcernRow.status)
        )
        counts = {s.value: 0 for s in CaseStatus}
        counts.update({status: int(n) for status, n in rows.all()})
        urgent = await self._s.scalar(
            select(func.count()).where(
                ConcernRow.tenant_id == self._tid,
                ConcernRow.severity == Severity.URGENT.value,
                ConcernRow.status != CaseStatus.CLOSED.value,
            )
        )
        counts["urgent_open"] = int(urgent or 0)
        return counts
