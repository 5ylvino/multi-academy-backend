from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.audit import bump_config_version, record_audit
from app.database import get_db
from app.deps import StaffContext, require_permissions
from app.models import FeatureFlag, Plan, PlanEntitlement
from app.schemas import EntitlementSet, PlanOut, PlanUpdate

router = APIRouter(prefix="/v1/plans", tags=["plans"])


def _get_plan(db: Session, key: str) -> Plan:
    plan = db.execute(
        select(Plan).options(selectinload(Plan.entitlements)).where(Plan.key == key)
    ).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown plan: {key}")
    return plan


@router.get("", response_model=list[PlanOut])
def list_plans(
    _: StaffContext = Depends(require_permissions("plans:read")),
    db: Session = Depends(get_db),
):
    return db.execute(
        select(Plan).options(selectinload(Plan.entitlements)).order_by(Plan.rank)
    ).scalars().all()


@router.patch("/{key}", response_model=PlanOut)
def update_plan(
    key: str,
    body: PlanUpdate,
    staff: StaffContext = Depends(require_permissions("plans:write")),
    db: Session = Depends(get_db),
):
    plan = _get_plan(db, key)
    before = {"price": plan.monthly_price_minor, "is_active": plan.is_active}
    for field in ("name", "description", "monthly_price_minor", "currency", "is_active"):
        value = getattr(body, field)
        if value is not None:
            setattr(plan, field, value)
    record_audit(
        db,
        action="plan.update",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="plan",
        target_id=key,
        before=before,
        after={"price": plan.monthly_price_minor, "is_active": plan.is_active},
    )
    db.commit()
    return _get_plan(db, key)


@router.put("/{key}/entitlements", response_model=PlanOut)
def set_entitlement(
    key: str,
    body: EntitlementSet,
    staff: StaffContext = Depends(require_permissions("plans:write")),
    db: Session = Depends(get_db),
):
    plan = _get_plan(db, key)
    flag = db.execute(
        select(FeatureFlag).where(FeatureFlag.key == body.feature_key)
    ).scalar_one_or_none()
    if flag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown flag: {body.feature_key}")

    entitlement = db.execute(
        select(PlanEntitlement).where(
            PlanEntitlement.plan_id == plan.id,
            PlanEntitlement.feature_key == body.feature_key,
        )
    ).scalar_one_or_none()
    before = {"enabled": entitlement.enabled, "quota": entitlement.quota} if entitlement else None
    if entitlement is None:
        entitlement = PlanEntitlement(plan_id=plan.id, feature_key=body.feature_key)
        db.add(entitlement)
    entitlement.enabled = body.enabled
    entitlement.quota = body.quota

    record_audit(
        db,
        action="plan.entitlement.set",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="plan",
        target_id=key,
        before=before,
        after={"feature_key": body.feature_key, "enabled": body.enabled, "quota": body.quota},
    )
    bump_config_version(db)
    db.commit()
    return _get_plan(db, key)
