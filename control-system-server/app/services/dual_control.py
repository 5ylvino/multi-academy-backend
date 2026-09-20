"""Dual-control helpers for destructive platform actions (Phase C)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.audit import record_audit
from app.config import get_settings
from app.models import DualControlRequest

settings = get_settings()


def dual_control_enabled() -> bool:
    if settings.dual_control_required is not None:
        return bool(settings.dual_control_required)
    return settings.environment == "production"


def create_request(
    db: Session,
    *,
    action: str,
    target_type: str,
    target_id: str,
    payload: dict,
    reason: str,
    staff_id: str,
    staff_email: str,
) -> DualControlRequest:
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.dual_control_expiry_hours)
    req = DualControlRequest(
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        payload=payload or {},
        reason=reason,
        status="pending",
        requested_by=staff_email,
        requested_by_id=staff_id,
        expires_at=expires,
    )
    db.add(req)
    record_audit(
        db,
        action="dual_control.request",
        actor_id=staff_id,
        actor_email=staff_email,
        target_type=target_type,
        target_id=str(target_id),
        reason=reason,
        after={"action": action, "payload": payload},
    )
    db.flush()
    return req
