"""Break-glass support sessions — ticket-bound, time-boxed (Phase B)."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.database import get_db
from app.deps import StaffContext, require_permissions
from app.models import SupportSession, Tenant
from app.schemas import SupportSessionCreate, SupportSessionOut

router = APIRouter(prefix="/v1/support", tags=["support"])

MAX_BREAK_GLASS_MINUTES = 60

ELEVATED_SCOPES = {
    "read": ["tenants:read", "support:read"],
    "elevated_pii": ["tenants:read", "support:read", "pii:read"],
    "write_support": ["tenants:read", "support:read", "pii:read", "support:write"],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _session_active(session: SupportSession) -> bool:
    if not session.is_active or session.revoked_at is not None:
        return False
    ends = session.ends_at if session.ends_at.tzinfo else session.ends_at.replace(tzinfo=timezone.utc)
    return ends >= _now()


@router.get("/sessions", response_model=list[SupportSessionOut])
def list_sessions(
    active_only: bool = True,
    _: StaffContext = Depends(require_permissions("tenants:read")),
    db: Session = Depends(get_db),
):
    stmt = select(SupportSession).order_by(SupportSession.id.desc())
    if active_only:
        stmt = stmt.where(SupportSession.is_active.is_(True))
    sessions = list(db.execute(stmt.limit(100)).scalars().all())
    now = _now()
    dirty = False
    for s in sessions:
        ends = s.ends_at if s.ends_at.tzinfo else s.ends_at.replace(tzinfo=timezone.utc)
        if s.is_active and ends < now:
            s.is_active = False
            s.revoke_reason = "expired"
            dirty = True
    if dirty:
        db.commit()
    return sessions


@router.get("/sessions/{session_id}/scope")
def get_session_scope(
    session_id: int,
    staff: StaffContext = Depends(require_permissions("tenants:read")),
    db: Session = Depends(get_db),
):
    """Document effective break-glass scope for an active support session.

    Elevated PII reads require an active session with access_level elevated_pii
    or write_support matching the session tenant. Enforced via require_support_elevation().
    """
    session = db.get(SupportSession, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    tenant = db.get(Tenant, session.tenant_id)
    active = _session_active(session)
    scopes = ELEVATED_SCOPES.get(session.access_level, ELEVATED_SCOPES["read"])
    return {
        "sessionId": session.id,
        "tenantId": session.tenant_id,
        "tenantSlug": tenant.slug if tenant else None,
        "ticketId": session.ticket_id,
        "accessLevel": session.access_level,
        "isActive": active,
        "staffEmail": session.staff_email,
        "requestingStaff": staff.email,
        "startsAt": session.starts_at.isoformat() if session.starts_at else None,
        "endsAt": session.ends_at.isoformat() if session.ends_at else None,
        "effectiveScopes": scopes,
        "maxDurationMinutes": MAX_BREAK_GLASS_MINUTES,
        "documentation": (
            "Use require_support_elevation(tenant_id, min_level) dependency on routes "
            "that expose PII. Session must be active, unrevoked, and bound to the tenant."
        ),
    }


@router.post(
    "/sessions",
    response_model=SupportSessionOut,
    status_code=status.HTTP_201_CREATED,
)
def open_session(
    body: SupportSessionCreate,
    staff: StaffContext = Depends(require_permissions("support:break_glass")),
    db: Session = Depends(get_db),
):
    tenant = db.get(Tenant, body.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    if not body.ticket_id.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ticket_id required")
    if not body.reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "reason required")

    duration = min(body.duration_minutes, MAX_BREAK_GLASS_MINUTES)
    starts = _now()
    session = SupportSession(
        tenant_id=body.tenant_id,
        ticket_id=body.ticket_id.strip(),
        reason=body.reason.strip(),
        staff_email=staff.email,
        access_level=body.access_level,
        starts_at=starts,
        ends_at=starts + timedelta(minutes=duration),
        is_active=True,
    )
    db.add(session)
    record_audit(
        db,
        action="support.break_glass.open",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="support_session",
        target_id=body.ticket_id,
        reason=body.reason,
        after={
            "tenant_id": body.tenant_id,
            "access_level": body.access_level,
            "duration_minutes": duration,
        },
    )
    db.commit()
    db.refresh(session)
    return session


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(
    session_id: int,
    reason: str = "revoked",
    staff: StaffContext = Depends(require_permissions("support:break_glass")),
    db: Session = Depends(get_db),
):
    session = db.get(SupportSession, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    session.is_active = False
    session.revoked_at = _now()
    session.revoke_reason = reason
    record_audit(
        db,
        action="support.break_glass.revoke",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="support_session",
        target_id=str(session_id),
        reason=reason,
    )
    db.commit()
