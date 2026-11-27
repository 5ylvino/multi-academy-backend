"""Everything a school client needs on start-up, in one call."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from masms.domain.identity.roles import Realm
from masms.domain.shared.errors import TenantUnavailable
from masms.use_cases.platform.feature_flags import ResolveTenantFeatures
from masms.use_cases.shared.ports import Cache, Scope, UnitOfWorkFactory
from masms.use_cases.shared.principal import Principal
from masms.use_cases.tenancy.manage_tenants import GetTenantRuntime


@dataclass(frozen=True, slots=True)
class SchoolBootstrap:
    tenant_id: UUID
    school_name: str
    slug: str
    status: str
    plan_code: str
    school_levels: list[str]
    config_version: int
    features: dict[str, bool]
    user_id: UUID
    roles: list[str]
    permissions: list[str]


class GetSchoolBootstrap:
    PROFILE_TTL_SECONDS = 300

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        cache: Cache,
        runtime: GetTenantRuntime,
        features: ResolveTenantFeatures,
    ) -> None:
        self._uow_factory = uow_factory
        self._cache = cache
        self._runtime = runtime
        self._features = features

    async def __call__(self, principal: Principal) -> SchoolBootstrap:
        principal.require_realm(Realm.SCHOOL)
        tenant_id = principal.school_tenant_id
        runtime = await self._runtime(tenant_id)
        if runtime is None or not runtime.serving:
            raise TenantUnavailable("This school account is not available.")

        profile_key = f"tenant:{tenant_id}:profile:c{runtime.config_version}"
        profile = await self._cache.get_json(profile_key)
        if not isinstance(profile, dict):
            async with self._uow_factory(Scope.registry(), read_only=True) as uow:
                tenant = await uow.tenants.get(tenant_id)
            if tenant is None:
                raise TenantUnavailable("This school account is not available.")
            profile = {
                "name": tenant.name,
                "slug": tenant.slug.value,
                "school_levels": sorted(level.value for level in tenant.school_levels),
            }
            await self._cache.set_json(profile_key, profile, self.PROFILE_TTL_SECONDS)

        features = await self._features(
            tenant_id, config_version=runtime.config_version, plan_code=runtime.plan_code
        )
        return SchoolBootstrap(
            tenant_id=tenant_id,
            school_name=str(profile["name"]),
            slug=str(profile["slug"]),
            status=runtime.status,
            plan_code=runtime.plan_code,
            school_levels=[str(level) for level in profile["school_levels"]],
            config_version=runtime.config_version,
            features=features,
            user_id=principal.user_id,
            roles=sorted(principal.roles),
            permissions=sorted(principal.permissions.grants),
        )
