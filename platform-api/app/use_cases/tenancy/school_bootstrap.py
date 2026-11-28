"""Everything a school client needs on start-up, in one call."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.domain.academics.sections import ordered
from app.domain.identity.roles import Realm
from app.domain.shared.errors import TenantUnavailable
from app.use_cases.academics.sections import scope_of
from app.use_cases.platform.feature_flags import ResolveTenantFeatures
from app.use_cases.shared.ports import Cache, Scope, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal
from app.use_cases.tenancy.manage_tenants import GetTenantRuntime


@dataclass(frozen=True, slots=True)
class SchoolBootstrap:
    tenant_id: UUID
    school_name: str
    slug: str
    status: str
    plan_code: str
    school_levels: list[str]
    timezone: str
    config_version: int
    features: dict[str, bool]
    user_id: UUID
    roles: list[str]
    permissions: list[str]
    #: Every section of the school, with its levels and the features it uses.
    sections: list[BootstrapSection]
    #: True for whole-school leaders and staff serving the whole school.
    whole_school: bool
    #: The sections this person works in (every section when whole_school).
    my_section_ids: list[UUID]


@dataclass(frozen=True, slots=True)
class BootstrapSection:
    id: UUID
    name: str
    levels: list[str]
    features: list[str]


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

        async with self._uow_factory(Scope.tenant(tenant_id), read_only=True) as uow:
            all_sections = await uow.sections.list_sections()
            scope = await scope_of(uow, principal)
        sections = [
            BootstrapSection(
                id=sec.id,
                name=sec.name,
                levels=[lvl.value for lvl in ordered(sec.levels)],
                features=sorted(f.value for f in sec.features),
            )
            for sec in all_sections
        ]
        mine = (
            [sec.id for sec in sections]
            if scope.whole_school
            else [sec.id for sec in sections if sec.id in scope.section_ids]
        )
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
            timezone=runtime.timezone,
            config_version=runtime.config_version,
            features=features,
            user_id=principal.user_id,
            roles=sorted(principal.roles),
            permissions=sorted(principal.permissions.grants),
            sections=sections,
            whole_school=scope.whole_school,
            my_section_ids=mine,
        )
