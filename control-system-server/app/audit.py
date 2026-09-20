from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, SystemState


def record_audit(
    db: Session,
    *,
    action: str,
    actor_type: str = "staff",
    actor_id: str = "",
    actor_email: str = "",
    target_type: str = "",
    target_id: str = "",
    reason: str = "",
    before: dict | None = None,
    after: dict | None = None,
    ip_address: str = "",
    request_id: str = "",
) -> AuditEvent:
    """Append an audit event. Caller commits."""
    event = AuditEvent(
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_email=actor_email,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        before=before,
        after=after,
        ip_address=ip_address,
        request_id=request_id,
    )
    db.add(event)
    return event


def bump_config_version(db: Session) -> int:
    """Increment the global runtime configVersion and enqueue outbox invalidate.

    Nest polls by configVersion / TTL today; outbox rows are the Phase B stub
    for a future Redis/pubsub or webhook push worker.
    """
    from app.services.outbox import enqueue_outbox
    from app.services.runtime_config import invalidate_runtime_config_cache

    state = _system_state(db)
    state.config_version += 1
    invalidate_runtime_config_cache()
    enqueue_outbox(
        db,
        event_type="config.invalidate",
        aggregate_type="system",
        aggregate_id="config_version",
        payload={"configVersion": state.config_version, "invalidateAll": True},
    )
    return state.config_version


def _system_state(db: Session) -> SystemState:
    """Single-row table — take the oldest row if a seed race created extras."""
    state = (
        db.execute(select(SystemState).order_by(SystemState.id.asc()))
        .scalars()
        .first()
    )
    if state is None:
        state = SystemState(config_version=1)
        db.add(state)
        db.flush()
    return state


def current_config_version(db: Session) -> int:
    state = (
        db.execute(select(SystemState).order_by(SystemState.id.asc()))
        .scalars()
        .first()
    )
    return state.config_version if state else 1
