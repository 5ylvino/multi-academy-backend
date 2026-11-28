"""Sections of a school and the staff assigned to them."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import String, cast, delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.academics.sections import Section, SectionFeature
from app.domain.academics.structure import SchoolLevelCode
from app.infrastructure.persistence.models.academics import (
    SectionFeatureRow,
    SectionLevelRow,
    SectionRoleRow,
    SectionRow,
    SectionStaffRow,
)
from app.infrastructure.persistence.repositories.scope_state import ScopeState


class SqlSectionRepository:
    def __init__(self, session: AsyncSession, scope: ScopeState) -> None:
        self._s = session
        self._scope = scope

    @property
    def _tid(self) -> UUID:
        if self._scope.tenant_id is None:
            raise RuntimeError("Sections need a tenant scope.")
        return self._scope.tenant_id

    async def list_sections(self) -> list[Section]:
        rows = (
            await self._s.scalars(select(SectionRow).where(SectionRow.tenant_id == self._tid))
        ).all()
        levels: dict[UUID, set[SchoolLevelCode]] = {}
        for lv in (
            await self._s.scalars(
                select(SectionLevelRow).where(SectionLevelRow.tenant_id == self._tid)
            )
        ).all():
            levels.setdefault(lv.section_id, set()).add(SchoolLevelCode(lv.school_level))
        features: dict[UUID, set[SectionFeature]] = {}
        for ft in (
            await self._s.scalars(
                select(SectionFeatureRow).where(SectionFeatureRow.tenant_id == self._tid)
            )
        ).all():
            features.setdefault(ft.section_id, set()).add(SectionFeature(ft.feature))
        order = {
            SchoolLevelCode.NURSERY: 0,
            SchoolLevelCode.PRIMARY: 1,
            SchoolLevelCode.SECONDARY: 2,
        }
        sections = [
            Section(
                id=r.id,
                tenant_id=r.tenant_id,
                name=r.name,
                code=r.code,
                levels=frozenset(levels.get(r.id, set())),
                created_at=r.created_at,
                features=frozenset(features.get(r.id, set())),
            )
            for r in rows
        ]
        return sorted(sections, key=lambda s: min((order[lv] for lv in s.levels), default=9))

    async def get_section(self, section_id: UUID) -> Section | None:
        return next((s for s in await self.list_sections() if s.id == section_id), None)

    async def lock(self) -> None:
        await self._s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"sections:{self._tid}"},
        )

    async def save_arrangement(self, sections: list[Section], removed: list[UUID]) -> None:
        tid = self._tid
        await self._s.execute(delete(SectionLevelRow).where(SectionLevelRow.tenant_id == tid))
        if removed:
            await self._s.execute(
                delete(SectionRow).where(SectionRow.tenant_id == tid, SectionRow.id.in_(removed))
            )
        # Codes follow the levels, so a regroup can swap codes between sections: clear them
        # first to keep the unique index happy.
        kept = [s.id for s in sections]
        if kept:
            await self._s.execute(
                update(SectionRow)
                .where(SectionRow.tenant_id == tid, SectionRow.id.in_(kept))
                .values(code=cast(SectionRow.id, String(40)))
            )
        for section in sections:
            stmt = insert(SectionRow).values(
                tenant_id=tid,
                id=section.id,
                name=section.name,
                code=section.code,
                created_at=section.created_at,
            )
            await self._s.execute(
                stmt.on_conflict_do_update(
                    index_elements=[SectionRow.tenant_id, SectionRow.id],
                    set_={"name": stmt.excluded.name, "code": stmt.excluded.code},
                )
            )
        rows = [
            {"tenant_id": tid, "school_level": level.value, "section_id": section.id}
            for section in sections
            for level in section.levels
        ]
        if rows:
            await self._s.execute(insert(SectionLevelRow).values(rows))
        if kept:
            await self._s.execute(
                delete(SectionFeatureRow).where(
                    SectionFeatureRow.tenant_id == tid, SectionFeatureRow.section_id.in_(kept)
                )
            )
        feature_rows = [
            {"tenant_id": tid, "section_id": section.id, "feature": f.value}
            for section in sections
            for f in section.features
        ]
        if feature_rows:
            await self._s.execute(insert(SectionFeatureRow).values(feature_rows))

    async def per_section_roles(self) -> set[str]:
        rows = await self._s.scalars(
            select(SectionRoleRow.role).where(SectionRoleRow.tenant_id == self._tid)
        )
        return set(rows.all())

    async def set_per_section_roles(self, roles: set[str]) -> None:
        await self._s.execute(delete(SectionRoleRow).where(SectionRoleRow.tenant_id == self._tid))
        if roles:
            await self._s.execute(
                insert(SectionRoleRow).values([{"tenant_id": self._tid, "role": r} for r in roles])
            )

    async def sections_of(self, user_id: UUID) -> set[UUID]:
        return (await self.staff_sections({user_id})).get(user_id, set())

    async def staff_of(self, section_id: UUID) -> set[UUID]:
        rows = await self._s.scalars(
            select(SectionStaffRow.user_id).where(
                SectionStaffRow.tenant_id == self._tid, SectionStaffRow.section_id == section_id
            )
        )
        return set(rows.all())

    async def staff_sections(self, user_ids: set[UUID]) -> dict[UUID, set[UUID]]:
        if not user_ids:
            return {}
        rows = await self._s.execute(
            select(SectionStaffRow.user_id, SectionStaffRow.section_id).where(
                SectionStaffRow.tenant_id == self._tid, SectionStaffRow.user_id.in_(user_ids)
            )
        )
        found: dict[UUID, set[UUID]] = {}
        for user_id, section_id in rows.all():
            found.setdefault(user_id, set()).add(section_id)
        return found

    async def assign(
        self, user_id: UUID, section_ids: set[UUID], *, by: UUID, now: datetime
    ) -> None:
        tid = self._tid
        await self._s.execute(
            delete(SectionStaffRow).where(
                SectionStaffRow.tenant_id == tid,
                SectionStaffRow.user_id == user_id,
                SectionStaffRow.section_id.not_in(section_ids) if section_ids else text("true"),
            )
        )
        if section_ids:
            await self._s.execute(
                insert(SectionStaffRow)
                .values(
                    [
                        {
                            "tenant_id": tid,
                            "section_id": s,
                            "user_id": user_id,
                            "assigned_by": by,
                            "assigned_at": now,
                        }
                        for s in section_ids
                    ]
                )
                .on_conflict_do_nothing()
            )

    async def release_staff(self, section_id: UUID) -> set[UUID]:
        people = await self.staff_of(section_id)
        await self._s.execute(
            delete(SectionStaffRow).where(
                SectionStaffRow.tenant_id == self._tid, SectionStaffRow.section_id == section_id
            )
        )
        return people

    async def add_staff(
        self, section_ids: set[UUID], user_ids: set[UUID], *, by: UUID, now: datetime
    ) -> None:
        rows = [
            {
                "tenant_id": self._tid,
                "section_id": s,
                "user_id": u,
                "assigned_by": by,
                "assigned_at": now,
            }
            for s in section_ids
            for u in user_ids
        ]
        if rows:
            await self._s.execute(insert(SectionStaffRow).values(rows).on_conflict_do_nothing())
