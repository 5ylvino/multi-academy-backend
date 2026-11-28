"""Repository ports for platform controls."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.platform.feature_flags import FeatureFlag, Plan


class FeatureFlagRepository(Protocol):
    async def list_all(self) -> list[FeatureFlag]: ...

    async def get(self, key: str) -> FeatureFlag | None: ...

    async def upsert(self, flag: FeatureFlag) -> None: ...

    async def overrides_for(self, tenant_id: UUID) -> dict[str, bool]: ...

    async def set_override(
        self, tenant_id: UUID, key: str, enabled: bool | None, actor_id: UUID
    ) -> None: ...


class PlanRepository(Protocol):
    async def get(self, code: str) -> Plan | None: ...

    async def list_all(self) -> list[Plan]: ...

    async def upsert(self, plan: Plan) -> None: ...
