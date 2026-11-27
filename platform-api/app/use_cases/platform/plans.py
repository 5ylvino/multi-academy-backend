"""Manage subscription plans and the features they include."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.identity.roles import Realm
from app.domain.platform.feature_flags import Plan, validate_flag_key
from app.domain.shared.errors import ValidationFailed
from app.use_cases.shared import cache_keys
from app.use_cases.shared.ports import AuditEntry, Cache, Scope, UnitOfWorkFactory
from app.use_cases.shared.principal import Principal

_CODE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


@dataclass(frozen=True, slots=True)
class PlanView:
    code: str
    name: str
    features: list[str]
    student_limit: int | None
    active: bool

    @classmethod
    def of(cls, plan: Plan) -> PlanView:
        return cls(
            code=plan.code,
            name=plan.name,
            features=sorted(plan.features),
            student_limit=plan.student_limit,
            active=plan.active,
        )


class ListPlans:
    def __init__(self, *, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def __call__(self, actor: Principal) -> list[PlanView]:
        actor.require_realm(Realm.PLATFORM)
        actor.require("plans:read")
        async with self._uow_factory(Scope.platform_wide(), read_only=True) as uow:
            plans = await uow.plans.list_all()
        return [PlanView.of(p) for p in sorted(plans, key=lambda p: p.code)]


@dataclass(frozen=True, slots=True)
class UpsertPlanCommand:
    code: str
    name: str
    features: list[str]
    student_limit: int | None
    active: bool


class UpsertPlan:
    def __init__(self, *, uow_factory: UnitOfWorkFactory, cache: Cache) -> None:
        self._uow_factory = uow_factory
        self._cache = cache

    async def __call__(self, actor: Principal, cmd: UpsertPlanCommand) -> PlanView:
        actor.require_realm(Realm.PLATFORM)
        actor.require("plans:write")
        if not _CODE.match(cmd.code):
            raise ValidationFailed(
                "Plan codes are lowercase words, like 'premium'.", details={"field": "code"}
            )
        if cmd.student_limit is not None and cmd.student_limit < 1:
            raise ValidationFailed(
                "Student limit must be positive.", details={"field": "student_limit"}
            )
        plan = Plan(
            code=cmd.code,
            name=cmd.name.strip() or cmd.code,
            features=frozenset(validate_flag_key(f) for f in cmd.features),
            student_limit=cmd.student_limit,
            active=cmd.active,
        )
        async with self._uow_factory(Scope.platform_wide()) as uow:
            await uow.plans.upsert(plan)
            uow.audit(
                AuditEntry(
                    action="plan.upserted",
                    actor_id=actor.user_id,
                    actor_realm=Realm.PLATFORM,
                    tenant_id=None,
                    target_type="plan",
                    target_id=plan.code,
                    metadata={"features": sorted(plan.features), "active": plan.active},
                )
            )
            await uow.commit()
        # Plan contents feed every school's feature resolution.
        await self._cache.bump_version(cache_keys.FLAGS_VERSION)
        return PlanView.of(plan)
