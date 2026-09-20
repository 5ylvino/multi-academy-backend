"""Dual-control request queue + execution for high-risk actions (Phase C)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import bump_config_version, record_audit
from app.database import get_db
from app.deps import StaffContext, client_ip, require_permissions
from app.models import DualControlRequest, FeatureFlag, ProviderConfig, SchoolBlacklistEntry, Subscription, Tenant
from app.models.dual_control import DUAL_ACTIONS
from app.schemas import DualControlDecide, DualControlOut
from app.services.dual_control import dual_control_enabled
from app.services.offboarding import schedule_offboard_job

router = APIRouter(prefix="/v1/dual-control", tags=["dual-control"])


def _expire_if_needed(req: DualControlRequest) -> bool:
    if req.status != "pending" or req.expires_at is None:
        return False
    expires = req.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        req.status = "expired"
        return True
    return False


def _execute(db: Session, req: DualControlRequest, staff: StaffContext, request: Request | None = None) -> None:
    """Apply the approved payload. Caller commits."""
    action = req.action
    payload = req.payload or {}
    ip = client_ip(request) if request else ""

    if action in ("tenant.blacklist", "tenant.unblacklist"):
        tenant_id = int(req.target_id)
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
        if action == "tenant.blacklist":
            before = tenant.status
            tenant.status = "blacklisted"
            tenant.status_message = "This school has been blocked from the platform. Contact support."
            tenant.status_reason = "abuse"
            for sub in db.execute(
                select(Subscription).where(
                    Subscription.tenant_id == tenant.id, Subscription.is_current.is_(True)
                )
            ).scalars():
                sub.status = "cancelled_blacklisted"
            email_domain = tenant.admin_email.split("@")[-1] if "@" in tenant.admin_email else ""
            db.add(
                SchoolBlacklistEntry(
                    tenant_id=tenant.id,
                    school_name_normalized=tenant.name.strip().lower(),
                    slug=tenant.slug,
                    admin_email=tenant.admin_email.lower(),
                    email_domain=email_domain.lower(),
                    phone=tenant.admin_phone,
                    reason=req.reason,
                    evidence=payload.get("evidence", ""),
                    severity=payload.get("severity", "high"),
                    permanent=bool(payload.get("permanent", True)),
                    created_by=req.requested_by,
                )
            )
            record_audit(
                db,
                action="tenant.blacklist",
                actor_id=staff.id,
                actor_email=staff.email,
                target_type="tenant",
                target_id=str(tenant_id),
                reason=f"dual-control#{req.id}: {req.reason}",
                before={"status": before},
                after={"status": "blacklisted"},
                ip_address=ip,
            )
        else:
            tenant.status = "active"
            tenant.status_message = ""
            tenant.status_reason = ""
            for entry in db.execute(
                select(SchoolBlacklistEntry).where(
                    SchoolBlacklistEntry.tenant_id == tenant.id,
                    SchoolBlacklistEntry.is_active.is_(True),
                )
            ).scalars():
                entry.is_active = False
            record_audit(
                db,
                action="tenant.unblacklist",
                actor_id=staff.id,
                actor_email=staff.email,
                target_type="tenant",
                target_id=str(tenant_id),
                reason=f"dual-control#{req.id}: {req.reason}",
                before={"status": "blacklisted"},
                after={"status": "active"},
                ip_address=ip,
            )

    elif action == "tenant.destroy":
        tenant_id = int(req.target_id)
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
        if tenant.legal_hold:
            raise HTTPException(status.HTTP_409_CONFLICT, "Tenant under legal hold — cannot destroy")
        before = tenant.status
        tenant.status = "archived"
        tenant.status_message = "Tenant archived pending offboard wipe."
        tenant.status_reason = "offboarding"
        record_audit(
            db,
            action="tenant.destroy",
            actor_id=staff.id,
            actor_email=staff.email,
            target_type="tenant",
            target_id=str(tenant_id),
            reason=f"dual-control#{req.id}: {req.reason}",
            before={"status": before},
            after={"status": "archived"},
            ip_address=ip,
        )

    elif action == "tenant.offboard":
        tenant_id = int(req.target_id)
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
        if tenant.legal_hold:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Tenant under legal hold — cannot offboard"
            )
        job = schedule_offboard_job(
            db,
            tenant=tenant,
            wipe_in_days=int(payload.get("wipe_in_days", 30)),
            notes=payload.get("notes", ""),
            created_by=req.requested_by,
        )
        record_audit(
            db,
            action="tenant.offboard.schedule",
            actor_id=staff.id,
            actor_email=staff.email,
            target_type="tenant",
            target_id=str(tenant_id),
            reason=f"dual-control#{req.id}: {req.reason}",
            after={"jobId": job.id, "wipeScheduledAt": str(job.wipe_scheduled_at)},
            ip_address=ip,
        )

    elif action in ("flag.kill_switch", "flag.kill_switch_lift"):
        key = req.target_id
        flag = db.execute(select(FeatureFlag).where(FeatureFlag.key == key)).scalar_one_or_none()
        if flag is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Flag not found")
        before = flag.kill_switch
        flag.kill_switch = bool(payload.get("enabled", action == "flag.kill_switch"))
        record_audit(
            db,
            action="flag.kill_switch",
            actor_id=staff.id,
            actor_email=staff.email,
            target_type="flag",
            target_id=key,
            reason=f"dual-control#{req.id}: {req.reason}",
            before={"kill_switch": before},
            after={"kill_switch": flag.kill_switch},
            ip_address=ip,
        )

    elif action in ("provider.switch_live", "provider.switch_from_live"):
        capability = payload.get("capability", "")
        tenant_ref = payload.get("tenant_id")
        if req.target_id.startswith("new:"):
            config = None
        else:
            config = db.get(ProviderConfig, int(req.target_id))
        if config is None:
            # The request deliberately did not pre-create the row; create it now
            # that a second operator has approved.
            config = db.execute(
                select(ProviderConfig).where(
                    ProviderConfig.capability == capability,
                    ProviderConfig.tenant_id.is_(None)
                    if tenant_ref is None
                    else ProviderConfig.tenant_id == tenant_ref,
                )
            ).scalar_one_or_none()
        if config is None:
            config = ProviderConfig(capability=capability, tenant_id=tenant_ref)
            db.add(config)
            db.flush()

        before = {"provider_id": config.provider_id, "mode": config.mode}
        if payload.get("provider_id"):
            config.provider_id = payload["provider_id"]
        config.mode = payload.get(
            "mode", "live" if action == "provider.switch_live" else "sandbox"
        )
        config.is_enabled = True
        if payload.get("settings") is not None:
            config.settings = payload["settings"]
        record_audit(
            db,
            action="provider.switch",
            actor_id=staff.id,
            actor_email=staff.email,
            target_type="provider",
            target_id=f"{config.capability}:{config.tenant_id or 'global'}",
            reason=f"dual-control#{req.id}: {req.reason}",
            before=before,
            after={"provider_id": config.provider_id, "mode": config.mode},
            ip_address=ip,
        )
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown dual-control action: {action}")

    req.status = "executed"
    req.executed_at = datetime.now(timezone.utc)
    bump_config_version(db)


@router.get("", response_model=list[DualControlOut])
def list_requests(
    status_filter: str | None = Query(default=None, alias="status"),
    _: StaffContext = Depends(require_permissions("dual_control:approve")),
    db: Session = Depends(get_db),
):
    stmt = select(DualControlRequest).order_by(DualControlRequest.created_at.desc())
    if status_filter:
        stmt = stmt.where(DualControlRequest.status == status_filter)
    rows = db.execute(stmt.limit(100)).scalars().all()
    dirty = False
    for r in rows:
        if _expire_if_needed(r):
            dirty = True
    if dirty:
        db.commit()
    return rows


@router.get("/required")
def dual_control_status(_: StaffContext = Depends(require_permissions("dashboard:read"))):
    return {
        "required": dual_control_enabled(),
        "environment": __import__("app.config", fromlist=["get_settings"]).get_settings().environment,
        "actions": list(DUAL_ACTIONS),
    }


@router.post("/{request_id}/decide", response_model=DualControlOut)
def decide(
    request_id: int,
    body: DualControlDecide,
    request: Request,
    staff: StaffContext = Depends(require_permissions("dual_control:approve")),
    db: Session = Depends(get_db),
):
    # Locked for the transaction: without this, two approvers hitting the button
    # at once both read status="pending" and the action executed twice.
    stmt = select(DualControlRequest).where(DualControlRequest.id == request_id)
    if db.bind is not None and db.bind.dialect.name != "sqlite":
        stmt = stmt.with_for_update()
    req = db.execute(stmt).scalar_one_or_none()
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    if _expire_if_needed(req):
        db.commit()
        raise HTTPException(status.HTTP_410_GONE, "Request expired")
    if req.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Request is {req.status}")
    if not (body.reason or "").strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "A decision reason is required and is audited"
        )
    if req.requested_by_id == staff.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Requester cannot approve their own dual-control request",
        )

    req.decided_at = datetime.now(timezone.utc)
    req.approved_by = staff.email
    req.approved_by_id = staff.id
    req.decision_reason = body.reason

    if not body.approve:
        req.status = "rejected"
        record_audit(
            db,
            action="dual_control.reject",
            actor_id=staff.id,
            actor_email=staff.email,
            target_type=req.target_type,
            target_id=req.target_id,
            reason=body.reason,
            after={"request_id": req.id, "action": req.action},
            ip_address=client_ip(request),
        )
        db.commit()
        db.refresh(req)
        return req

    req.status = "approved"
    record_audit(
        db,
        action="dual_control.approve",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type=req.target_type,
        target_id=req.target_id,
        reason=body.reason,
        after={"request_id": req.id, "action": req.action},
        ip_address=client_ip(request),
    )
    _execute(db, req, staff, request)
    db.commit()
    db.refresh(req)
    return req
