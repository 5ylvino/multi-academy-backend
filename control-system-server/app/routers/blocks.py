from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import bump_config_version, record_audit
from app.database import get_db
from app.deps import StaffContext, client_ip, require_permissions
from app.models import Block, SchoolBlacklistEntry, Tenant
from app.models.enforcement import BLOCK_TARGET_TYPES
from app.schemas import BlacklistEntryOut, BlockCreate, BlockOut

router = APIRouter(prefix="/v1/blocks", tags=["blocks"])


@router.get("", response_model=list[BlockOut])
def list_blocks(
    active_only: bool = True,
    _: StaffContext = Depends(require_permissions("blocks:read")),
    db: Session = Depends(get_db),
):
    stmt = select(Block).order_by(Block.created_at.desc())
    if active_only:
        stmt = stmt.where(Block.is_active.is_(True))
    return db.execute(stmt).scalars().all()


@router.post("", response_model=BlockOut, status_code=status.HTTP_201_CREATED)
def create_block(
    body: BlockCreate,
    request: Request,
    staff: StaffContext = Depends(require_permissions("blocks:write")),
    db: Session = Depends(get_db),
):
    if body.target_type not in BLOCK_TARGET_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown target type: {body.target_type}")
    if not body.reason:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reason required")
    if body.tenant_id is not None and db.get(Tenant, body.tenant_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    block = Block(
        target_type=body.target_type,
        value=body.value,
        tenant_id=body.tenant_id,
        action=body.action,
        reason=body.reason,
        evidence=body.evidence,
        severity=body.severity,
        expires_at=body.expires_at,
        created_by=staff.email,
    )
    db.add(block)
    record_audit(
        db,
        action="block.create",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="block",
        target_id=f"{body.target_type}:{body.value}",
        reason=body.reason,
        after={"tenant_id": body.tenant_id, "action": body.action, "severity": body.severity},
        ip_address=client_ip(request),
    )
    bump_config_version(db)
    db.commit()
    db.refresh(block)
    return block


@router.delete("/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
def lift_block(
    block_id: int,
    request: Request,
    reason: str = "",
    staff: StaffContext = Depends(require_permissions("blocks:write")),
    db: Session = Depends(get_db),
):
    block = db.get(Block, block_id)
    if block is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Block not found")
    block.is_active = False
    record_audit(
        db,
        action="block.lift",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="block",
        target_id=f"{block.target_type}:{block.value}",
        reason=reason,
        ip_address=client_ip(request),
    )
    bump_config_version(db)
    db.commit()


@router.post("/import")
def import_blocks_csv(
    request: Request,
    csv_text: str = Body(..., media_type="text/plain"),
    staff: StaffContext = Depends(require_permissions("blocks:write")),
    db: Session = Depends(get_db),
):
    """Bulk import blocks from CSV text — one target per line: email, ip, or user:value."""
    if not csv_text.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "CSV body required")

    created = []
    skipped = []
    for line_no, raw in enumerate(csv_text.strip().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        target_type = "email"
        value = line
        reason = "Bulk CSV import"
        tenant_id = None

        if len(parts) >= 2 and parts[0] in BLOCK_TARGET_TYPES:
            target_type = parts[0]
            value = parts[1]
            if len(parts) >= 3:
                reason = parts[2]
            if len(parts) >= 4 and parts[3].isdigit():
                tenant_id = int(parts[3])
        elif "@" in line:
            target_type = "email"
            value = line.lower()
        elif line.replace(".", "").replace(":", "").isdigit() or "/" in line:
            target_type = "ip"
            value = line
        elif ":" in line:
            target_type, value = line.split(":", 1)
            if target_type not in BLOCK_TARGET_TYPES:
                skipped.append({"line": line_no, "reason": f"unknown type {target_type}"})
                continue

        if target_type not in BLOCK_TARGET_TYPES:
            skipped.append({"line": line_no, "reason": "could not infer target type"})
            continue

        block = Block(
            target_type=target_type,
            value=value,
            tenant_id=tenant_id,
            action="deny",
            reason=reason,
            created_by=staff.email,
        )
        db.add(block)
        db.flush()
        created.append({"id": block.id, "targetType": target_type, "value": value})

    if created:
        record_audit(
            db,
            action="block.import",
            actor_id=staff.id,
            actor_email=staff.email,
            target_type="block",
            target_id=f"csv:{len(created)}",
            after={"created": len(created), "skipped": len(skipped)},
            ip_address=client_ip(request),
        )
        bump_config_version(db)
    db.commit()
    return {"created": created, "skipped": skipped, "count": len(created)}


@router.get("/schools", response_model=list[BlacklistEntryOut])
def school_blacklist(
    active_only: bool = True,
    _: StaffContext = Depends(require_permissions("blocks:read")),
    db: Session = Depends(get_db),
):
    stmt = select(SchoolBlacklistEntry).order_by(SchoolBlacklistEntry.created_at.desc())
    if active_only:
        stmt = stmt.where(SchoolBlacklistEntry.is_active.is_(True))
    return db.execute(stmt).scalars().all()
