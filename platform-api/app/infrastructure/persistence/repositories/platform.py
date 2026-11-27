"""Platform repositories: flags, overrides and plans."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.platform.feature_flags import FeatureFlag, Plan
from app.infrastructure.persistence.models.platform import (
    FeatureFlagRow,
    PlanRow,
    TenantFlagOverrideRow,
)


def _flag(row: FeatureFlagRow) -> FeatureFlag:
    return FeatureFlag(
        key=row.key,
        name=row.name,
        description=row.description,
        default_enabled=row.default_enabled,
        kill_switch=row.kill_switch,
        rollout_percent=row.rollout_percent,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


class SqlFeatureFlagRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list_all(self) -> list[FeatureFlag]:
        return [_flag(r) for r in (await self._s.scalars(select(FeatureFlagRow))).all()]

    async def get(self, key: str) -> FeatureFlag | None:
        row = await self._s.get(FeatureFlagRow, key)
        return _flag(row) if row else None

    async def upsert(self, flag: FeatureFlag) -> None:
        values = {
            "key": flag.key,
            "name": flag.name,
            "description": flag.description,
            "default_enabled": flag.default_enabled,
            "kill_switch": flag.kill_switch,
            "rollout_percent": flag.rollout_percent,
            "updated_at": flag.updated_at,
            "updated_by": flag.updated_by,
        }
        stmt = insert(FeatureFlagRow).values(**values)
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[FeatureFlagRow.key],
                set_={k: stmt.excluded[k] for k in values if k != "key"},
            )
        )

    async def overrides_for(self, tenant_id: UUID) -> dict[str, bool]:
        rows = await self._s.execute(
            select(TenantFlagOverrideRow.flag_key, TenantFlagOverrideRow.enabled).where(
                TenantFlagOverrideRow.tenant_id == tenant_id
            )
        )
        return {key: bool(enabled) for key, enabled in rows.all()}

    async def set_override(
        self, tenant_id: UUID, key: str, enabled: bool | None, actor_id: UUID
    ) -> None:
        if enabled is None:
            await self._s.execute(
                delete(TenantFlagOverrideRow).where(
                    TenantFlagOverrideRow.tenant_id == tenant_id,
                    TenantFlagOverrideRow.flag_key == key,
                )
            )
            return
        stmt = insert(TenantFlagOverrideRow).values(
            tenant_id=tenant_id, flag_key=key, enabled=enabled, updated_by=actor_id
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[TenantFlagOverrideRow.tenant_id, TenantFlagOverrideRow.flag_key],
                set_={"enabled": stmt.excluded.enabled, "updated_by": stmt.excluded.updated_by},
            )
        )


def _plan(row: PlanRow) -> Plan:
    return Plan(
        code=row.code,
        name=row.name,
        features=frozenset(row.features or ()),
        student_limit=row.student_limit,
        active=row.active,
    )


class SqlPlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, code: str) -> Plan | None:
        row = await self._s.get(PlanRow, code)
        return _plan(row) if row else None

    async def list_all(self) -> list[Plan]:
        return [_plan(r) for r in (await self._s.scalars(select(PlanRow))).all()]

    async def upsert(self, plan: Plan) -> None:
        values = {
            "code": plan.code,
            "name": plan.name,
            "features": sorted(plan.features),
            "student_limit": plan.student_limit,
            "active": plan.active,
        }
        stmt = insert(PlanRow).values(**values)
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[PlanRow.code],
                set_={k: stmt.excluded[k] for k in values if k != "code"},
            )
        )
