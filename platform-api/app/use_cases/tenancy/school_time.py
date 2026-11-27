"""School-local time, from the cached tenant runtime (no database hit per request)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.shared.errors import TenantUnavailable
from app.use_cases.shared.ports import Clock
from app.use_cases.tenancy.manage_tenants import GetTenantRuntime


class TenantSchoolTime:
    def __init__(self, *, runtime: GetTenantRuntime, clock: Clock) -> None:
        self._runtime = runtime
        self._clock = clock

    async def now_local(self, tenant_id: UUID) -> datetime:
        runtime = await self._runtime(tenant_id)
        if runtime is None:
            raise TenantUnavailable("This school account is not available.")
        return self._clock.now().astimezone(ZoneInfo(runtime.timezone))
