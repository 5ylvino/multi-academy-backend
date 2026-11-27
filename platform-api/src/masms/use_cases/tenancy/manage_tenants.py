"""Owner-console use cases for schools: list, inspect, suspend and reinstate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from masms.domain.identity.roles import Realm
from masms.domain.shared.errors import NotFound, ValidationFailed
from masms.domain.shared.value_objects import Page
from masms.domain.tenancy.tenant import Tenant, TenantStatus
from masms.use_cases.shared import cache_keys
from masms.use_cases.shared.pagination import clamp_limit
from masms.use_cases.shared.ports import AuditEntry, Cache, Clock, Scope, UnitOfWorkFactory
from masms.use_cases.shared.principal import Principal


@dataclass(frozen=True, slots=True)
class TenantView:
    id: UUID
    slug: str
    name: str
    status: str
    status_reason: str | None
    plan_code: str
    school_levels: list[str]
    country_code: str
    contact_email: str
    created_at: datetime
    trial_ends_at: datetime | None
    config_version: int

    @classmethod
    def of(cls, tenant: Tenant) -> TenantView:
        return cls(
            id=tenant.id,
            slug=tenant.slug.value,
            name=tenant.name,
            status=tenant.status.value,
            status_reason=tenant.status_reason,
            plan_code=tenant.plan_code,
            school_levels=sorted(level.value for level in tenant.school_levels),
            country_code=tenant.country_code,
            contact_email=tenant.contact_email,
            created_at=tenant.created_at,
            trial_ends_at=tenant.trial_ends_at,
            config_version=tenant.config_version,
        )


class ListTenants:
    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def __call__(
        self,
        actor: Principal,
        *,
        status: str | None,
        search: str | None,
        cursor: str | None,
        limit: int | None,
    ) -> Page[TenantView]:
        actor.require_realm(Realm.PLATFORM)
        actor.require("tenants:read")
        try:
            status_filter = TenantStatus(status) if status else None
        except ValueError as exc:
            raise ValidationFailed("Unknown status.", details={"field": "status"}) from exc
        async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
            page = await uow.tenants.list(
                status=status_filter,
                search=(search or "").strip() or None,
                cursor=cursor,
                limit=clamp_limit(limit),
            )
        return Page(items=[TenantView.of(t) for t in page.items], next_cursor=page.next_cursor)


class GetTenant:
    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def __call__(self, actor: Principal, tenant_id: UUID) -> TenantView:
        actor.require_realm(Realm.PLATFORM)
        actor.require("tenants:read")
        async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
            tenant = await uow.tenants.get(tenant_id)
        if tenant is None:
            raise NotFound("School not found.")
        return TenantView.of(tenant)


@dataclass(frozen=True, slots=True)
class ChangeTenantStatusCommand:
    tenant_id: UUID
    action: str  # "suspend" | "reinstate" | "activate"
    reason: str | None = None


class ChangeTenantStatus:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, cache: Cache, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._cache = cache
        self._clock = clock

    async def __call__(self, actor: Principal, cmd: ChangeTenantStatusCommand) -> TenantView:
        actor.require_realm(Realm.PLATFORM)
        actor.require("tenants:write")
        async with self._uow_factory(Scope.platform_wide()) as uow:
            tenant = await uow.tenants.get(cmd.tenant_id)
            if tenant is None:
                raise NotFound("School not found.")
            previous = tenant.status
            if cmd.action == "suspend":
                tenant.suspend(cmd.reason or "")
            elif cmd.action == "reinstate":
                tenant.reinstate(self._clock.now())
            elif cmd.action == "activate":
                tenant.activate()
            else:
                raise ValidationFailed("Unknown action.", details={"field": "action"})
            await uow.tenants.save(tenant)
            uow.track(tenant)
            uow.audit(
                AuditEntry(
                    action=f"tenant.{cmd.action}",
                    actor_id=actor.user_id,
                    actor_realm=Realm.PLATFORM,
                    tenant_id=tenant.id,
                    target_type="tenant",
                    target_id=str(tenant.id),
                    metadata={
                        "from": previous.value,
                        "to": tenant.status.value,
                        "reason": cmd.reason,
                    },
                )
            )
            await uow.commit()
        await self._cache.delete(cache_keys.tenant_runtime(tenant.id))
        return TenantView.of(tenant)


@dataclass(frozen=True, slots=True)
class TenantRuntime:
    """The small slice of tenant state every school request checks. Cached for a few seconds."""

    tenant_id: UUID
    status: str
    serving: bool
    config_version: int
    plan_code: str
    timezone: str = "Africa/Lagos"


class GetTenantRuntime:
    TTL_SECONDS = 15

    def __init__(self, *, uow_factory: UnitOfWorkFactory, cache: Cache) -> None:
        self._uow_factory = uow_factory
        self._cache = cache

    async def __call__(self, tenant_id: UUID) -> TenantRuntime | None:
        key = cache_keys.tenant_runtime(tenant_id)
        cached = await self._cache.get_json(key)
        if isinstance(cached, dict):
            return TenantRuntime(
                tenant_id=tenant_id,
                status=str(cached["status"]),
                serving=bool(cached["serving"]),
                config_version=int(cached["config_version"]),
                plan_code=str(cached["plan_code"]),
                timezone=str(cached.get("timezone", "Africa/Lagos")),
            )
        async with self._uow_factory(Scope.registry(), read_only=True) as uow:
            tenant = await uow.tenants.get(tenant_id)
        if tenant is None:
            return None
        runtime = TenantRuntime(
            tenant_id=tenant.id,
            status=tenant.status.value,
            serving=tenant.is_serving,
            config_version=tenant.config_version,
            plan_code=tenant.plan_code,
            timezone=tenant.timezone,
        )
        await self._cache.set_json(
            key,
            {
                "status": runtime.status,
                "serving": runtime.serving,
                "config_version": runtime.config_version,
                "plan_code": runtime.plan_code,
                "timezone": runtime.timezone,
            },
            self.TTL_SECONDS,
        )
        return runtime
