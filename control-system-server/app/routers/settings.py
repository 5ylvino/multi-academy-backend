"""Settings: MFA policy, IP allowlist, admin session revoke, rate-limit budgets."""

from datetime import datetime, timezone
import ipaddress

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.config import get_settings
from app.database import get_db
from app.deps import StaffContext, require_permissions
from app.models import IpAllowlistEntry, SecuritySettings, StaffSession
from app.schemas import (
    IpAllowlistCreate,
    IpAllowlistOut,
    SecuritySettingsOut,
    SecuritySettingsUpdate,
    StaffSessionOut,
)

router = APIRouter(prefix="/v1/settings", tags=["settings"])


def _ensure_security(db: Session) -> SecuritySettings:
    row = db.get(SecuritySettings, 1)
    if row is None:
        settings = get_settings()
        row = SecuritySettings(
            id=1,
            mfa_required=False,
            ip_allowlist_enforced=settings.ip_allowlist_enforced,
            idle_timeout_minutes=settings.staff_idle_timeout_minutes,
        )
        db.add(row)
        db.flush()
    return row


@router.get("/security", response_model=SecuritySettingsOut)
def get_security_settings(
    _: StaffContext = Depends(require_permissions("platform_users:read")),
    db: Session = Depends(get_db),
):
    return _ensure_security(db)


@router.patch("/security", response_model=SecuritySettingsOut)
def update_security_settings(
    body: SecuritySettingsUpdate,
    staff: StaffContext = Depends(require_permissions("platform_users:write")),
    db: Session = Depends(get_db),
):
    row = _ensure_security(db)
    before = {
        "mfa_required": row.mfa_required,
        "ip_allowlist_enforced": row.ip_allowlist_enforced,
        "idle_timeout_minutes": row.idle_timeout_minutes,
    }
    if body.mfa_required is not None:
        row.mfa_required = body.mfa_required
    if body.ip_allowlist_enforced is not None:
        row.ip_allowlist_enforced = body.ip_allowlist_enforced
    if body.idle_timeout_minutes is not None:
        row.idle_timeout_minutes = body.idle_timeout_minutes
    row.updated_by = staff.email
    record_audit(
        db,
        action="settings.security.update",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="security_settings",
        target_id="1",
        reason=body.reason,
        before=before,
        after={
            "mfa_required": row.mfa_required,
            "ip_allowlist_enforced": row.ip_allowlist_enforced,
            "idle_timeout_minutes": row.idle_timeout_minutes,
        },
    )
    db.commit()
    db.refresh(row)
    return row


@router.get("/ip-allowlist", response_model=list[IpAllowlistOut])
def list_ip_allowlist(
    _: StaffContext = Depends(require_permissions("platform_users:read")),
    db: Session = Depends(get_db),
):
    return db.execute(
        select(IpAllowlistEntry).order_by(IpAllowlistEntry.id.desc())
    ).scalars().all()


@router.post(
    "/ip-allowlist",
    response_model=IpAllowlistOut,
    status_code=status.HTTP_201_CREATED,
)
def add_ip_allowlist(
    body: IpAllowlistCreate,
    staff: StaffContext = Depends(require_permissions("platform_users:write")),
    db: Session = Depends(get_db),
):
    try:
        network = ipaddress.ip_network(body.cidr.strip(), strict=False)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid CIDR: {exc}") from exc
    cidr = str(network)
    exists = db.execute(
        select(IpAllowlistEntry).where(IpAllowlistEntry.cidr == cidr)
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "CIDR already listed")
    entry = IpAllowlistEntry(cidr=cidr, label=body.label, created_by=staff.email)
    db.add(entry)
    record_audit(
        db,
        action="settings.ip_allowlist.add",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="ip_allowlist",
        after={"cidr": cidr, "label": body.label},
    )
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/ip-allowlist/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_ip_allowlist(
    entry_id: int,
    staff: StaffContext = Depends(require_permissions("platform_users:write")),
    db: Session = Depends(get_db),
):
    entry = db.get(IpAllowlistEntry, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entry not found")
    entry.is_active = False
    record_audit(
        db,
        action="settings.ip_allowlist.deactivate",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="ip_allowlist",
        target_id=str(entry_id),
        before={"cidr": entry.cidr},
    )
    db.commit()


@router.get("/sessions", response_model=list[StaffSessionOut])
def list_all_sessions(
    _: StaffContext = Depends(require_permissions("platform_users:write")),
    db: Session = Depends(get_db),
):
    return db.execute(
        select(StaffSession)
        .where(StaffSession.revoked_at.is_(None))
        .order_by(StaffSession.created_at.desc())
        .limit(200)
    ).scalars().all()


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_revoke_session(
    session_id: int,
    staff: StaffContext = Depends(require_permissions("platform_users:write")),
    db: Session = Depends(get_db),
):
    session = db.get(StaffSession, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    if session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        session.revoke_reason = "admin_revoke"
        record_audit(
            db,
            action="settings.session.revoke",
            actor_id=staff.id,
            actor_email=staff.email,
            target_type="staff_session",
            target_id=str(session_id),
            after={"user_id": session.user_id},
        )
        db.commit()


@router.get("/rate-limits")
def rate_limit_budgets(
    _: StaffContext = Depends(require_permissions("dashboard:read")),
):
    s = get_settings()
    return {
        "windowSeconds": 60,
        "budgets": {
            "staff": s.rate_limit_staff_per_minute,
            "m2m": s.rate_limit_m2m_per_minute,
            "auth": s.rate_limit_auth_per_minute,
        },
        "notes": "Separate budgets: /v1/* staff, /internal/* m2m, /v1/auth/login|refresh auth.",
    }
