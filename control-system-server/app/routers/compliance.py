from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.database import get_db
from app.deps import StaffContext, require_permissions
from app.models import DsrRequest, Subprocessor, Tenant
from app.models.compliance import DSR_STATUSES, DSR_TYPES
from app.schemas import (
    DsrCreate,
    DsrOut,
    DsrUpdate,
    SubprocessorCreate,
    SubprocessorOut,
    SubprocessorUpdate,
)

router = APIRouter(prefix="/v1/compliance", tags=["compliance"])


@router.get("/dsr", response_model=list[DsrOut])
def list_dsr(
    status_filter: str | None = Query(default=None, alias="status"),
    tenant_id: int | None = None,
    _: StaffContext = Depends(require_permissions("compliance:read")),
    db: Session = Depends(get_db),
):
    stmt = select(DsrRequest).order_by(DsrRequest.created_at.desc())
    if status_filter:
        stmt = stmt.where(DsrRequest.status == status_filter)
    if tenant_id is not None:
        stmt = stmt.where(DsrRequest.tenant_id == tenant_id)
    return db.execute(stmt.limit(200)).scalars().all()


@router.post("/dsr", response_model=DsrOut, status_code=status.HTTP_201_CREATED)
def create_dsr(
    body: DsrCreate,
    staff: StaffContext = Depends(require_permissions("compliance:write")),
    db: Session = Depends(get_db),
):
    if body.request_type not in DSR_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"request_type must be one of {DSR_TYPES}")
    if body.tenant_id is not None:
        tenant = db.get(Tenant, body.tenant_id)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
        if body.request_type == "delete" and tenant.legal_hold:
            req = DsrRequest(
                tenant_id=body.tenant_id,
                request_type=body.request_type,
                subject_email=str(body.subject_email).lower(),
                subject_name=body.subject_name,
                status="blocked_legal_hold",
                notes=f"Blocked: legal hold — {tenant.legal_hold_reason or 'no reason'}. {body.notes}",
                evidence_ref=body.evidence_ref,
                due_at=body.due_at,
                created_by=staff.email,
            )
            db.add(req)
            record_audit(
                db,
                action="dsr.create_blocked",
                actor_id=staff.id,
                actor_email=staff.email,
                target_type="dsr",
                target_id=str(body.subject_email),
                reason="legal_hold",
                after={"request_type": body.request_type, "tenant_id": body.tenant_id},
            )
            db.commit()
            db.refresh(req)
            return req

    req = DsrRequest(
        tenant_id=body.tenant_id,
        request_type=body.request_type,
        subject_email=str(body.subject_email).lower(),
        subject_name=body.subject_name,
        status="received",
        notes=body.notes,
        evidence_ref=body.evidence_ref,
        due_at=body.due_at,
        created_by=staff.email,
    )
    db.add(req)
    record_audit(
        db,
        action="dsr.create",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="dsr",
        after={"request_type": body.request_type, "subject": str(body.subject_email)},
    )
    db.commit()
    db.refresh(req)
    return req


@router.patch("/dsr/{dsr_id}", response_model=DsrOut)
def update_dsr(
    dsr_id: int,
    body: DsrUpdate,
    staff: StaffContext = Depends(require_permissions("compliance:write")),
    db: Session = Depends(get_db),
):
    req = db.get(DsrRequest, dsr_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DSR not found")
    before = {"status": req.status}
    if body.status is not None:
        if body.status not in DSR_STATUSES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"status must be one of {DSR_STATUSES}")
        if body.status == "completed" and req.request_type == "delete" and req.tenant_id:
            tenant = db.get(Tenant, req.tenant_id)
            if tenant and tenant.legal_hold:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Cannot complete delete DSR while tenant is under legal hold",
                )
        req.status = body.status
        if body.status == "completed":
            req.completed_at = datetime.now(timezone.utc)
    if body.notes is not None:
        req.notes = body.notes
    if body.assigned_to is not None:
        req.assigned_to = body.assigned_to
    if body.export_uri is not None:
        req.export_uri = body.export_uri
    record_audit(
        db,
        action="dsr.update",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="dsr",
        target_id=str(dsr_id),
        reason=body.reason,
        before=before,
        after={"status": req.status},
    )
    db.commit()
    db.refresh(req)
    return req


@router.post("/dsr/{dsr_id}/fulfill", response_model=DsrOut)
def fulfill_dsr(
    dsr_id: int,
    staff: StaffContext = Depends(require_permissions("compliance:write")),
    db: Session = Depends(get_db),
):
    """Fulfill DSR — writes a local export package JSON and marks completed."""
    import json
    from pathlib import Path

    req = db.get(DsrRequest, dsr_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DSR not found")
    if req.status in ("completed", "rejected", "blocked_legal_hold"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"DSR already {req.status}")

    before = {"status": req.status, "export_uri": req.export_uri}
    export_dir = Path("exports") / "dsr" / str(req.id)
    export_dir.mkdir(parents=True, exist_ok=True)
    package = {
        "dsrId": req.id,
        "requestType": req.request_type,
        "subjectEmail": req.subject_email,
        "subjectName": req.subject_name,
        "tenantId": req.tenant_id,
        "fulfilledAt": datetime.now(timezone.utc).isoformat(),
        "fulfilledBy": staff.email,
        "note": "Control-plane metadata package. School tenant PII export is orchestrated separately under break-glass.",
        "contents": {
            "auditHint": "Search audit by subject email / tenant for access logs",
            "retention": "Follow NDPR retention policy before wipe",
        },
    }
    file_path = export_dir / f"{req.request_type}-package.json"
    file_path.write_text(json.dumps(package, indent=2), encoding="utf-8")
    req.export_uri = str(file_path.resolve())
    req.status = "completed"
    req.completed_at = datetime.now(timezone.utc)
    if not req.assigned_to:
        req.assigned_to = staff.email
    req.notes = (req.notes or "") + f"\n[Fulfilled {req.completed_at.isoformat()}] export at {req.export_uri}"

    record_audit(
        db,
        action="dsr.fulfill",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="dsr",
        target_id=str(dsr_id),
        before=before,
        after={"status": req.status, "export_uri": req.export_uri},
    )
    db.commit()
    db.refresh(req)
    return req



@router.get("/subprocessors", response_model=list[SubprocessorOut])
def list_subprocessors(
    include_inactive: bool = Query(default=False),
    _: StaffContext = Depends(require_permissions("compliance:read")),
    db: Session = Depends(get_db),
):
    stmt = select(Subprocessor).order_by(Subprocessor.name)
    if not include_inactive:
        stmt = stmt.where(Subprocessor.is_active.is_(True))
    return db.execute(stmt).scalars().all()


@router.post(
    "/subprocessors",
    response_model=SubprocessorOut,
    status_code=status.HTTP_201_CREATED,
)
def create_subprocessor(
    body: SubprocessorCreate,
    staff: StaffContext = Depends(require_permissions("compliance:write")),
    db: Session = Depends(get_db),
):
    row = Subprocessor(
        name=body.name,
        purpose=body.purpose,
        region=body.region,
        dpa_url=body.dpa_url,
        is_active=True,
    )
    db.add(row)
    record_audit(
        db,
        action="subprocessor.create",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="subprocessor",
        after={"name": body.name, "region": body.region},
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/subprocessors/{sub_id}", response_model=SubprocessorOut)
def update_subprocessor(
    sub_id: int,
    body: SubprocessorUpdate,
    staff: StaffContext = Depends(require_permissions("compliance:write")),
    db: Session = Depends(get_db),
):
    row = db.get(Subprocessor, sub_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subprocessor not found")
    before = {"name": row.name, "is_active": row.is_active, "region": row.region}
    if body.name is not None:
        row.name = body.name
    if body.purpose is not None:
        row.purpose = body.purpose
    if body.region is not None:
        row.region = body.region
    if body.dpa_url is not None:
        row.dpa_url = body.dpa_url
    if body.is_active is not None:
        row.is_active = body.is_active
    record_audit(
        db,
        action="subprocessor.update",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="subprocessor",
        target_id=str(sub_id),
        reason=body.reason,
        before=before,
        after={"name": row.name, "is_active": row.is_active, "region": row.region},
    )
    db.commit()
    db.refresh(row)
    return row


@router.get("/residency")
def residency_summary(
    _: StaffContext = Depends(require_permissions("compliance:read")),
    db: Session = Depends(get_db),
):
    tenants = db.execute(select(Tenant)).scalars().all()
    by_tag: dict[str, int] = {}
    legal_holds = 0
    for t in tenants:
        tag = t.residency_tag or t.region or "unknown"
        by_tag[tag] = by_tag.get(tag, 0) + 1
        if t.legal_hold:
            legal_holds += 1
    return {
        "byResidencyTag": by_tag,
        "legalHoldCount": legal_holds,
        "totalTenants": len(tenants),
    }
