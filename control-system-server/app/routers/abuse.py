"""Abuse cases + signals with simple auto-block threshold (Phase B)."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import bump_config_version, record_audit
from app.database import get_db
from app.deps import StaffContext, require_permissions
from app.models import AbuseCase, AbuseSignal, Block
from app.schemas import (
    AbuseCaseCreate,
    AbuseCaseOut,
    AbuseCaseUpdate,
    AbuseSignalCreate,
    AbuseSignalOut,
)

router = APIRouter(prefix="/v1/abuse", tags=["abuse"])

# Map signal types → block target_type for auto-block stub
_SIGNAL_TARGET = {
    "login_anomaly": "ip",
    "api_abuse": "ip",
    "payment_fraud": "user",
    "sms_burst": "capability",
}


@router.get("/cases", response_model=list[AbuseCaseOut])
def list_cases(
    status_filter: str | None = Query(default=None, alias="status"),
    _: StaffContext = Depends(require_permissions("abuse:read")),
    db: Session = Depends(get_db),
):
    stmt = select(AbuseCase).order_by(AbuseCase.id.desc())
    if status_filter:
        stmt = stmt.where(AbuseCase.status == status_filter)
    return db.execute(stmt.limit(100)).scalars().all()


@router.post("/cases", response_model=AbuseCaseOut, status_code=status.HTTP_201_CREATED)
def create_case(
    body: AbuseCaseCreate,
    staff: StaffContext = Depends(require_permissions("abuse:write")),
    db: Session = Depends(get_db),
):
    case = AbuseCase(
        tenant_id=body.tenant_id,
        title=body.title,
        severity=body.severity,
        summary=body.summary,
        created_by=staff.email,
    )
    db.add(case)
    record_audit(
        db,
        action="abuse.case.create",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="abuse_case",
        after=body.model_dump(),
    )
    db.commit()
    db.refresh(case)
    return case


@router.patch("/cases/{case_id}", response_model=AbuseCaseOut)
def update_case(
    case_id: int,
    body: AbuseCaseUpdate,
    staff: StaffContext = Depends(require_permissions("abuse:write")),
    db: Session = Depends(get_db),
):
    case = db.get(AbuseCase, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found")
    before = {"status": case.status, "severity": case.severity}
    for field in ("status", "severity", "summary", "assigned_to"):
        val = getattr(body, field)
        if val is not None:
            setattr(case, field, val)
    record_audit(
        db,
        action="abuse.case.update",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="abuse_case",
        target_id=str(case_id),
        reason=body.reason,
        before=before,
        after={"status": case.status, "severity": case.severity},
    )
    db.commit()
    db.refresh(case)
    return case


@router.get("/signals", response_model=list[AbuseSignalOut])
def list_signals(
    case_id: int | None = Query(default=None),
    _: StaffContext = Depends(require_permissions("abuse:read")),
    db: Session = Depends(get_db),
):
    stmt = select(AbuseSignal).order_by(AbuseSignal.id.desc())
    if case_id is not None:
        stmt = stmt.where(AbuseSignal.case_id == case_id)
    return db.execute(stmt.limit(200)).scalars().all()


@router.post("/signals", response_model=AbuseSignalOut, status_code=status.HTTP_201_CREATED)
def create_signal(
    body: AbuseSignalCreate,
    staff: StaffContext = Depends(require_permissions("abuse:write")),
    db: Session = Depends(get_db),
):
    signal = AbuseSignal(
        case_id=body.case_id,
        tenant_id=body.tenant_id,
        signal_type=body.signal_type,
        value=body.value,
        score=body.score,
        evidence=body.evidence,
    )
    db.add(signal)
    db.flush()

    # Simple auto-block: sum scores for same tenant+value+type; if >= threshold, block.
    score_stmt = select(func.coalesce(func.sum(AbuseSignal.score), 0)).where(
        AbuseSignal.signal_type == body.signal_type,
        AbuseSignal.value == body.value,
    )
    if body.tenant_id is not None:
        score_stmt = score_stmt.where(AbuseSignal.tenant_id == body.tenant_id)
    total = db.execute(score_stmt).scalar_one()

    if int(total) >= body.auto_block_threshold and body.value:
        target_type = _SIGNAL_TARGET.get(body.signal_type, "ip")
        block_value = body.value
        if body.signal_type == "sms_burst":
            target_type = "capability"
            block_value = "comms.sms"
        existing = db.execute(
            select(Block).where(
                Block.target_type == target_type,
                Block.value == block_value,
                Block.is_active.is_(True),
                Block.tenant_id == body.tenant_id,
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                Block(
                    target_type=target_type,
                    value=block_value,
                    tenant_id=body.tenant_id,
                    action="deny",
                    reason=f"Auto-block from abuse signal score={total}",
                    evidence=body.evidence,
                    severity="high",
                    created_by=staff.email,
                )
            )
            signal.auto_blocked = True
            bump_config_version(db)

    record_audit(
        db,
        action="abuse.signal.create",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="abuse_signal",
        target_id=str(signal.id),
        after={**body.model_dump(), "auto_blocked": signal.auto_blocked, "score_total": int(total)},
    )
    db.commit()
    db.refresh(signal)
    return signal
