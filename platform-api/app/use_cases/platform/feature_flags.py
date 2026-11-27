"""Manage feature flags and resolve them for a school."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID

from app.domain.identity.roles import Realm
from app.domain.platform.feature_flags import (
    FeatureFlag,
    FlagContext,
    resolve_all,
    validate_flag_key,
)
from app.domain.shared.errors import FeatureDisabled, NotFound
from app.use_cases.shared import cache_keys
from app.use_cases.shared.ports import AuditEntry, Cache, Clock, Scope, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal


@dataclass(frozen=True, slots=True)
class FlagView:
    key: str
    name: str
    description: str
    default_enabled: bool
    kill_switch: bool
    rollout_percent: int
    updated_at: datetime

    @classmethod
    def of(cls, flag: FeatureFlag) -> FlagView:
        return cls(
            key=flag.key,
            name=flag.name,
            description=flag.description,
            default_enabled=flag.default_enabled,
            kill_switch=flag.kill_switch,
            rollout_percent=flag.rollout_percent,
            updated_at=flag.updated_at,
        )


class ListFlags:
    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def __call__(self, actor: Principal) -> list[FlagView]:
        actor.require_realm(Realm.PLATFORM)
        actor.require("flags:read")
        async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
            flags = await uow.flags.list_all()
        return [FlagView.of(f) for f in sorted(flags, key=lambda f: f.key)]


@dataclass(frozen=True, slots=True)
class UpsertFlagCommand:
    key: str
    name: str
    description: str
    default_enabled: bool
    kill_switch: bool
    rollout_percent: int


class UpsertFlag:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, cache: Cache, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._cache = cache
        self._clock = clock

    async def __call__(self, actor: Principal, cmd: UpsertFlagCommand) -> FlagView:
        actor.require_realm(Realm.PLATFORM)
        actor.require("flags:write")
        flag = FeatureFlag(
            key=validate_flag_key(cmd.key),
            name=cmd.name.strip() or cmd.key,
            description=cmd.description.strip(),
            default_enabled=cmd.default_enabled,
            kill_switch=cmd.kill_switch,
            rollout_percent=cmd.rollout_percent,
            updated_at=self._clock.now(),
            updated_by=actor.user_id,
        )
        async with self._uow_factory(Scope.platform_wide()) as uow:
            before = await uow.flags.get(flag.key)
            await uow.flags.upsert(flag)
            uow.audit(
                AuditEntry(
                    action="flag.upserted",
                    actor_id=actor.user_id,
                    actor_realm=Realm.PLATFORM,
                    tenant_id=None,
                    target_type="feature_flag",
                    target_id=flag.key,
                    metadata={
                        "before": asdict(FlagView.of(before)) if before else None,
                        "after": {
                            k: v for k, v in asdict(FlagView.of(flag)).items() if k != "updated_at"
                        },
                    },
                )
            )
            await uow.commit()
        await self._cache.bump_version(cache_keys.FLAGS_VERSION)
        return FlagView.of(flag)


@dataclass(frozen=True, slots=True)
class SetFlagOverrideCommand:
    tenant_id: UUID
    key: str
    enabled: bool | None  # None removes the override


class SetFlagOverride:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, cache: Cache) -> None:
        self._uow_factory = uow_factory
        self._cache = cache

    async def __call__(self, actor: Principal, cmd: SetFlagOverrideCommand) -> None:
        actor.require_realm(Realm.PLATFORM)
        actor.require("flags:write")
        async with self._uow_factory(Scope.platform_wide()) as uow:
            tenant = await uow.tenants.get(cmd.tenant_id)
            if tenant is None:
                raise NotFound("School not found.")
            if await uow.flags.get(cmd.key) is None:
                raise NotFound("Flag not found.")
            await uow.flags.set_override(tenant.id, cmd.key, cmd.enabled, actor.user_id)
            tenant.config_version += 1
            await uow.tenants.save(tenant)
            uow.audit(
                AuditEntry(
                    action="flag.override_set",
                    actor_id=actor.user_id,
                    actor_realm=Realm.PLATFORM,
                    tenant_id=tenant.id,
                    target_type="feature_flag",
                    target_id=cmd.key,
                    metadata={"enabled": cmd.enabled},
                )
            )
            await uow.commit()
        await self._cache.delete(cache_keys.tenant_runtime(cmd.tenant_id))


class ResolveTenantFeatures:
    """Effective flags for one school, cached under a key that changes with any input."""

    TTL_SECONDS = 300

    def __init__(self, *, uow_factory: UnitOfWorkFactory, cache: Cache) -> None:
        self._uow_factory = uow_factory
        self._cache = cache

    async def __call__(
        self, tenant_id: UUID, *, config_version: int, plan_code: str
    ) -> dict[str, bool]:
        flags_version = await self._cache.get_version(cache_keys.FLAGS_VERSION)
        key = cache_keys.tenant_features(tenant_id, config_version, flags_version)
        cached = await self._cache.get_json(key)
        if isinstance(cached, dict):
            return {str(k): bool(v) for k, v in cached.items()}
        async with self._uow_factory(Scope.registry(), read_only=True) as uow:
            flags = await uow.flags.list_all()
            overrides = await uow.flags.overrides_for(tenant_id)
            plan = await uow.plans.get(plan_code)
        resolved = resolve_all(
            flags, FlagContext(tenant_id=tenant_id, plan=plan, overrides=overrides)
        )
        await self._cache.set_json(key, resolved, self.TTL_SECONDS)
        return resolved

    async def require(
        self, tenant_id: UUID, key: str, *, config_version: int, plan_code: str
    ) -> None:
        features = await self(tenant_id, config_version=config_version, plan_code=plan_code)
        if not features.get(key, False):
            raise FeatureDisabled(
                "This feature is not enabled for your school.", details={"feature": key}
            )
