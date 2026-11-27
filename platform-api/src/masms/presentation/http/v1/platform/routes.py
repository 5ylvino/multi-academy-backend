"""Feature flags and plans (owner console)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Path, status

from masms.presentation.http.dependencies.auth import PlatformPrincipal
from masms.presentation.http.dependencies.context import UseCasesDep
from masms.presentation.http.v1.platform.schemas import (
    FlagOverrideRequest,
    FlagResponse,
    PlanResponse,
    UpsertFlagRequest,
    UpsertPlanRequest,
)
from masms.use_cases.platform.feature_flags import SetFlagOverrideCommand, UpsertFlagCommand
from masms.use_cases.platform.plans import UpsertPlanCommand

router = APIRouter(prefix="/platform", tags=["platform flags and plans"])

FlagKey = Path(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.]*$")


@router.get("/flags", response_model=list[FlagResponse])
async def list_flags(principal: PlatformPrincipal, uc: UseCasesDep) -> list[FlagResponse]:
    return [FlagResponse.model_validate(f) for f in await uc.list_flags(principal)]


@router.put("/flags/{key}", response_model=FlagResponse)
async def upsert_flag(
    body: UpsertFlagRequest, principal: PlatformPrincipal, uc: UseCasesDep, key: str = FlagKey
) -> FlagResponse:
    view = await uc.upsert_flag(
        principal,
        UpsertFlagCommand(
            key=key,
            name=body.name,
            description=body.description,
            default_enabled=body.default_enabled,
            kill_switch=body.kill_switch,
            rollout_percent=body.rollout_percent,
        ),
    )
    return FlagResponse.model_validate(view)


@router.put("/tenants/{tenant_id}/flags/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def set_flag_override(
    tenant_id: UUID,
    body: FlagOverrideRequest,
    principal: PlatformPrincipal,
    uc: UseCasesDep,
    key: str = FlagKey,
) -> None:
    await uc.set_flag_override(
        principal, SetFlagOverrideCommand(tenant_id=tenant_id, key=key, enabled=body.enabled)
    )


@router.get("/plans", response_model=list[PlanResponse])
async def list_plans(principal: PlatformPrincipal, uc: UseCasesDep) -> list[PlanResponse]:
    return [PlanResponse.model_validate(p) for p in await uc.list_plans(principal)]


@router.put("/plans/{code}", response_model=PlanResponse)
async def upsert_plan(
    code: str, body: UpsertPlanRequest, principal: PlatformPrincipal, uc: UseCasesDep
) -> PlanResponse:
    view = await uc.upsert_plan(
        principal,
        UpsertPlanCommand(
            code=code,
            name=body.name,
            features=body.features,
            student_limit=body.student_limit,
            active=body.active,
        ),
    )
    return PlanResponse.model_validate(view)
