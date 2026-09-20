"""Provider-neutral school fee splitting and payroll account routing."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import bump_config_version, record_audit
from app.database import get_db
from app.deps import StaffContext, require_permissions
from app.models import FeeSplitRule, PayrollAccount, PayrollRun, Tenant
from app.schemas import (
    FeeSplitRuleOut,
    FeeSplitRuleSet,
    PayrollAccountOut,
    PayrollAccountSet,
    PayrollRunCreate,
    PayrollRunDecision,
    PayrollRunOut,
)

router = APIRouter(prefix="/v1/financial", tags=["financial-routing"])


def _get_payroll_run_for_tenant(db: Session, tenant_id: int, run_id: int) -> PayrollRun | None:
    return db.execute(
        select(PayrollRun).where(
            PayrollRun.id == run_id,
            PayrollRun.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()


@router.get("/{tenant_id}/fee-splits", response_model=list[FeeSplitRuleOut])
def list_fee_splits(
    tenant_id: int,
    _: StaffContext = Depends(require_permissions("billing:read")),
    db: Session = Depends(get_db),
):
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    return db.execute(
        select(FeeSplitRule)
        .where(FeeSplitRule.tenant_id == tenant_id)
        .order_by(FeeSplitRule.fee_type)
    ).scalars().all()


@router.put("/{tenant_id}/fee-splits", response_model=FeeSplitRuleOut)
def set_fee_split(
    tenant_id: int,
    body: FeeSplitRuleSet,
    staff: StaffContext = Depends(require_permissions("billing:write")),
    db: Session = Depends(get_db),
):
    if body.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tenant id mismatch")
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    rule = db.execute(
        select(FeeSplitRule).where(
            FeeSplitRule.tenant_id == tenant_id,
            FeeSplitRule.fee_type == body.fee_type,
        )
    ).scalar_one_or_none()
    if rule is None:
        rule = FeeSplitRule(tenant_id=tenant_id, fee_type=body.fee_type)
        db.add(rule)
    rule.provider_id = body.provider_id
    rule.currency = body.currency.upper()
    rule.allocations = [item.model_dump() for item in body.allocations]
    rule.is_active = True
    rule.created_by = staff.email
    record_audit(
        db,
        action="financial.fee_split.set",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="fee_split_rule",
        target_id=f"{tenant_id}:{body.fee_type}",
        after={"provider_id": body.provider_id, "allocations": rule.allocations},
    )
    bump_config_version(db)
    db.commit()
    db.refresh(rule)
    return rule


@router.get("/{tenant_id}/payroll-accounts", response_model=list[PayrollAccountOut])
def list_payroll_accounts(
    tenant_id: int,
    _: StaffContext = Depends(require_permissions("billing:read")),
    db: Session = Depends(get_db),
):
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    return db.execute(
        select(PayrollAccount)
        .where(PayrollAccount.tenant_id == tenant_id)
        .order_by(PayrollAccount.account_role)
    ).scalars().all()


@router.put("/{tenant_id}/payroll-accounts", response_model=PayrollAccountOut)
def set_payroll_account(
    tenant_id: int,
    body: PayrollAccountSet,
    staff: StaffContext = Depends(require_permissions("billing:write")),
    db: Session = Depends(get_db),
):
    if body.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tenant id mismatch")
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    account = db.execute(
        select(PayrollAccount).where(
            PayrollAccount.tenant_id == tenant_id,
            PayrollAccount.account_role == body.account_role,
        )
    ).scalar_one_or_none()
    if account is None:
        account = PayrollAccount(tenant_id=tenant_id, account_role=body.account_role)
        db.add(account)
    account.provider_id = body.provider_id
    account.account_reference = body.account_reference
    account.currency = body.currency.upper()
    account.is_active = True
    account.created_by = staff.email
    record_audit(
        db,
        action="financial.payroll_account.set",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="payroll_account",
        target_id=f"{tenant_id}:{body.account_role}",
        after={"provider_id": body.provider_id, "currency": account.currency},
    )
    bump_config_version(db)
    db.commit()
    db.refresh(account)
    return account


@router.get("/{tenant_id}/payroll-runs", response_model=list[PayrollRunOut])
def list_payroll_runs(
    tenant_id: int,
    _: StaffContext = Depends(require_permissions("billing:read")),
    db: Session = Depends(get_db),
):
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    return db.execute(
        select(PayrollRun)
        .where(PayrollRun.tenant_id == tenant_id)
        .order_by(PayrollRun.id.desc())
    ).scalars().all()


@router.post("/{tenant_id}/payroll-runs", response_model=PayrollRunOut, status_code=status.HTTP_201_CREATED)
def create_payroll_run(
    tenant_id: int,
    body: PayrollRunCreate,
    staff: StaffContext = Depends(require_permissions("billing:write")),
    db: Session = Depends(get_db),
):
    if body.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tenant id mismatch")
    if db.get(Tenant, tenant_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    salary_account = db.execute(
        select(PayrollAccount).where(
            PayrollAccount.tenant_id == tenant_id,
            PayrollAccount.account_role == "salary",
            PayrollAccount.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if salary_account is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Configure an active salary account first")
    existing = db.execute(
        select(PayrollRun).where(
            PayrollRun.tenant_id == tenant_id,
            PayrollRun.period_key == body.period_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Payroll run already exists for this period")
    run = PayrollRun(
        tenant_id=tenant_id,
        period_key=body.period_key,
        provider_id=body.provider_id,
        salary_account_id=salary_account.id,
        gross_minor=body.gross_minor,
        currency=body.currency.upper(),
        status="pending_approval",
        created_by=staff.email,
        notes=body.notes,
    )
    db.add(run)
    record_audit(
        db,
        action="financial.payroll_run.create",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="payroll_run",
        target_id=f"{tenant_id}:{body.period_key}",
        after={"gross_minor": body.gross_minor, "salary_account_id": salary_account.id},
    )
    db.commit()
    db.refresh(run)
    return run


@router.post("/{tenant_id}/payroll-runs/{run_id}/decision", response_model=PayrollRunOut)
def decide_payroll_run(
    tenant_id: int,
    run_id: int,
    body: PayrollRunDecision,
    staff: StaffContext = Depends(require_permissions("billing:write")),
    db: Session = Depends(get_db),
):
    run = _get_payroll_run_for_tenant(db, tenant_id, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payroll run not found")
    if run.status != "pending_approval":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Payroll run is {run.status}")
    if run.created_by == staff.email:
        raise HTTPException(status.HTTP_409_CONFLICT, "Payroll creator cannot approve the same run")
    if not body.reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Decision reason required")
    run.status = "approved" if body.approve else "failed"
    run.approved_by = staff.email
    run.notes = f"{run.notes}\nDecision: {body.reason}".strip()
    record_audit(
        db,
        action="financial.payroll_run.approve" if body.approve else "financial.payroll_run.reject",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="payroll_run",
        target_id=str(run.id),
        reason=body.reason,
        after={"status": run.status},
    )
    db.commit()
    db.refresh(run)
    return run
