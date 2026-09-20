"""Staff API — tutoring marketplace policy (control console only)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audit import bump_config_version, record_audit
from app.database import get_db
from app.deps import StaffContext, require_permissions
from app.models import Tenant
from app.models.tutoring import TutoringPolicy
from app.schemas import TutoringPolicyOut, TutoringPolicySet
from app.services.tutoring_policy import get_tutoring_policy_row, tutoring_policy_payload

router = APIRouter(prefix="/v1/tenants", tags=["tutoring-policy"])


def _tenant_or_404(db: Session, tenant_id: int) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    return tenant


@router.get("/{tenant_id}/tutoring-policy", response_model=TutoringPolicyOut)
def get_tutoring_policy(
    tenant_id: int,
    _: StaffContext = Depends(require_permissions("billing:read")),
    db: Session = Depends(get_db),
):
    _tenant_or_404(db, tenant_id)
    return TutoringPolicyOut(**tutoring_policy_payload(db, tenant_id))


@router.put("/{tenant_id}/tutoring-policy", response_model=TutoringPolicyOut)
def set_tutoring_policy(
    tenant_id: int,
    body: TutoringPolicySet,
    staff: StaffContext = Depends(require_permissions("billing:write")),
    db: Session = Depends(get_db),
):
    if body.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tenant id mismatch")
    _tenant_or_404(db, tenant_id)

    row = get_tutoring_policy_row(db, tenant_id)
    if row is None:
        row = TutoringPolicy(tenant_id=tenant_id)
        db.add(row)
    row.platform_fee_percent = body.platformFeePercent
    row.default_currency = body.defaultCurrency.upper()
    row.allow_external_tutors = body.allowExternalTutors
    row.marketplace_enabled = body.marketplaceEnabled
    row.updated_by = staff.email

    record_audit(
        db,
        action="tutoring.policy.set",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="tutoring_policy",
        target_id=str(tenant_id),
        after={
            "platform_fee_percent": row.platform_fee_percent,
            "default_currency": row.default_currency,
            "allow_external_tutors": row.allow_external_tutors,
            "marketplace_enabled": row.marketplace_enabled,
        },
    )
    bump_config_version(db)
    db.commit()
    return TutoringPolicyOut(**tutoring_policy_payload(db, tenant_id))
