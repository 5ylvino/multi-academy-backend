from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import bump_config_version, record_audit
from app.database import get_db
from app.deps import StaffContext, client_ip, require_permissions
from app.models import FeatureFlag, FlagCohortRollout, FlagOverride, Plan, Tenant
from app.schemas import (
    CohortRolloutOut,
    CohortRolloutSet,
    FlagDeprecateRequest,
    FlagOut,
    FlagUpdate,
    KillSwitchRequest,
    OverrideOut,
    OverrideSet,
)
from app.services.dual_control import create_request, dual_control_enabled

router = APIRouter(prefix="/v1/flags", tags=["feature-flags"])


def _get_flag(db: Session, key: str) -> FeatureFlag:
    flag = db.execute(select(FeatureFlag).where(FeatureFlag.key == key)).scalar_one_or_none()
    if flag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown flag: {key}")
    return flag


@router.get("", response_model=list[FlagOut])
def list_flags(
    _: StaffContext = Depends(require_permissions("flags:read")),
    db: Session = Depends(get_db),
):
    return db.execute(select(FeatureFlag).order_by(FeatureFlag.key)).scalars().all()


@router.patch("/{key}", response_model=FlagOut)
def update_flag(
    key: str,
    body: FlagUpdate,
    staff: StaffContext = Depends(require_permissions("flags:write")),
    db: Session = Depends(get_db),
):
    flag = _get_flag(db, key)
    before = {"default_enabled": flag.default_enabled, "min_plan": flag.min_plan, "status": flag.status}
    for field in ("name", "description", "default_enabled", "min_plan", "status"):
        value = getattr(body, field)
        if value is not None:
            setattr(flag, field, value)
    record_audit(
        db,
        action="flag.update",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="flag",
        target_id=key,
        before=before,
        after={"default_enabled": flag.default_enabled, "min_plan": flag.min_plan, "status": flag.status},
    )
    bump_config_version(db)
    db.commit()
    db.refresh(flag)
    return flag


@router.post("/{key}/kill-switch", response_model=FlagOut)
def set_kill_switch(
    key: str,
    body: KillSwitchRequest,
    request: Request,
    staff: StaffContext = Depends(require_permissions("flags:write")),
    db: Session = Depends(get_db),
):
    if not body.reason:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reason required for kill switch changes")
    flag = _get_flag(db, key)

    if flag.kill_switch == body.enabled:
        return flag

    # Both directions need approval. Only setting the switch was gated before, so
    # a single operator could quietly re-enable a feature that had been killed
    # for causing an incident.
    if dual_control_enabled():
        req = create_request(
            db,
            action="flag.kill_switch" if body.enabled else "flag.kill_switch_lift",
            target_type="flag",
            target_id=key,
            payload={"enabled": body.enabled},
            reason=body.reason,
            staff_id=staff.id,
            staff_email=staff.email,
        )
        db.commit()
        raise HTTPException(
            status.HTTP_202_ACCEPTED,
            f"Dual-control required to {'set' if body.enabled else 'lift'} this kill "
            f"switch. Pending approval id={req.id}",
        )

    before = flag.kill_switch
    flag.kill_switch = body.enabled
    record_audit(
        db,
        action="flag.kill_switch",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="flag",
        target_id=key,
        reason=body.reason,
        before={"kill_switch": before},
        after={"kill_switch": body.enabled},
        ip_address=client_ip(request),
    )
    bump_config_version(db)
    db.commit()
    db.refresh(flag)
    return flag


@router.post("/{key}/deprecate", response_model=FlagOut)
def deprecate_flag(
    key: str,
    body: FlagDeprecateRequest,
    staff: StaffContext = Depends(require_permissions("flags:write")),
    db: Session = Depends(get_db),
):
    flag = _get_flag(db, key)
    before = {"status": flag.status, "removal_date": flag.removal_date.isoformat() if flag.removal_date else None}
    flag.status = "deprecated"
    flag.deprecation_note = body.note or body.reason
    flag.removal_date = body.removal_date
    flag.deprecated_at = datetime.now(timezone.utc)
    record_audit(
        db,
        action="flag.deprecate",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="flag",
        target_id=key,
        reason=body.reason or body.note,
        before=before,
        after={
            "status": "deprecated",
            "removal_date": body.removal_date.isoformat(),
            "note": flag.deprecation_note,
        },
    )
    bump_config_version(db)
    db.commit()
    db.refresh(flag)
    return flag


@router.get("/rollouts/cohort", response_model=list[CohortRolloutOut])
def list_cohort_rollouts(
    _: StaffContext = Depends(require_permissions("flags:read")),
    db: Session = Depends(get_db),
):
    return db.execute(select(FlagCohortRollout).order_by(FlagCohortRollout.feature_key)).scalars().all()


@router.put("/rollouts/cohort", response_model=CohortRolloutOut)
def set_cohort_rollout(
    body: CohortRolloutSet,
    staff: StaffContext = Depends(require_permissions("flags:write")),
    db: Session = Depends(get_db),
):
    _get_flag(db, body.feature_key)
    row = db.execute(
        select(FlagCohortRollout).where(FlagCohortRollout.feature_key == body.feature_key)
    ).scalar_one_or_none()
    before = None
    if row is None:
        row = FlagCohortRollout(feature_key=body.feature_key)
        db.add(row)
    else:
        before = {
            "percentage": row.percentage,
            "cohort_tags": row.cohort_tags,
            "enabled": row.enabled,
        }
    row.percentage = body.percentage
    row.cohort_tags = body.cohort_tags
    row.enabled = body.enabled
    row.reason = body.reason
    row.created_by = staff.email
    record_audit(
        db,
        action="flag.cohort_rollout.set",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="flag",
        target_id=body.feature_key,
        reason=body.reason,
        before=before,
        after={"percentage": body.percentage, "cohort_tags": body.cohort_tags, "enabled": body.enabled},
    )
    bump_config_version(db)
    db.commit()
    db.refresh(row)
    return row


@router.get("/overrides", response_model=list[OverrideOut])
def list_overrides(
    _: StaffContext = Depends(require_permissions("flags:read")),
    db: Session = Depends(get_db),
):
    return db.execute(select(FlagOverride).order_by(FlagOverride.updated_at.desc())).scalars().all()


@router.put("/overrides", response_model=OverrideOut)
def set_override(
    body: OverrideSet,
    staff: StaffContext = Depends(require_permissions("flags:write")),
    db: Session = Depends(get_db),
):
    _get_flag(db, body.feature_key)

    plan_id = None
    tenant_id = None
    if body.scope == "plan":
        if not body.plan_key:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "plan_key required for plan scope")
        plan = db.execute(select(Plan).where(Plan.key == body.plan_key)).scalar_one_or_none()
        if plan is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown plan: {body.plan_key}")
        plan_id = plan.id
    elif body.scope == "tenant":
        if body.tenant_id is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "tenant_id required for tenant scope")
        if db.get(Tenant, body.tenant_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
        tenant_id = body.tenant_id
    elif body.scope != "global":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "scope must be global | plan | tenant")

    override = db.execute(
        select(FlagOverride).where(
            FlagOverride.feature_key == body.feature_key,
            FlagOverride.scope == body.scope,
            FlagOverride.plan_id.is_(plan_id) if plan_id is None else FlagOverride.plan_id == plan_id,
            FlagOverride.tenant_id.is_(tenant_id)
            if tenant_id is None
            else FlagOverride.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()

    before = {"enabled": override.enabled} if override else None
    if override is None:
        override = FlagOverride(
            feature_key=body.feature_key,
            scope=body.scope,
            plan_id=plan_id,
            tenant_id=tenant_id,
        )
        db.add(override)
    override.enabled = body.enabled
    override.reason = body.reason
    override.created_by = staff.email

    record_audit(
        db,
        action="flag.override.set",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="flag",
        target_id=body.feature_key,
        reason=body.reason,
        before=before,
        after={"scope": body.scope, "enabled": body.enabled, "tenant_id": tenant_id},
    )
    bump_config_version(db)
    db.commit()
    db.refresh(override)
    return override


@router.delete("/overrides/{override_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_override(
    override_id: int,
    staff: StaffContext = Depends(require_permissions("flags:write")),
    db: Session = Depends(get_db),
):
    override = db.get(FlagOverride, override_id)
    if override is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Override not found")
    record_audit(
        db,
        action="flag.override.delete",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="flag",
        target_id=override.feature_key,
        before={"scope": override.scope, "enabled": override.enabled, "tenant_id": override.tenant_id},
    )
    db.delete(override)
    bump_config_version(db)
    db.commit()
