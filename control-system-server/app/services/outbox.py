"""Outbox append. Delivery is handled by outbox_worker."""

import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import OutboxEvent

logger = logging.getLogger("control.outbox")
_warned_no_delivery = False


def _auto_publish_default() -> bool:
    """Whether to mark an event delivered without attempting delivery.

    Only ever true in development with no webhook configured. The previous
    implementation used `not outbox_worker_enabled()`, which in production
    without REDIS_URL evaluated to True — so every config invalidation was
    stamped delivered and silently dropped, and schools kept serving stale
    feature flags and provider credentials indefinitely.
    """
    global _warned_no_delivery
    settings = get_settings()
    if (settings.nest_config_webhook_url or "").strip():
        return False
    if settings.environment == "development":
        return True
    if not _warned_no_delivery:
        _warned_no_delivery = True
        logger.error(
            "NEST_CONFIG_WEBHOOK_URL is not configured in %s — config invalidation "
            "events will queue as pending and schools will only pick up changes on "
            "TTL expiry. Set the webhook URL to enable push invalidation.",
            settings.environment,
        )
    return False


def enqueue_outbox(
    db: Session,
    *,
    event_type: str,
    aggregate_type: str = "",
    aggregate_id: str = "",
    payload: dict | None = None,
    auto_publish_stub: bool | None = None,
) -> OutboxEvent:
    """Append an outbox row. Caller commits."""
    if auto_publish_stub is None:
        auto_publish_stub = _auto_publish_default()
    now = datetime.now(timezone.utc)
    event = OutboxEvent(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=json.dumps(payload or {}),
        published=auto_publish_stub,
        published_at=now if auto_publish_stub else None,
        delivery_status="delivered" if auto_publish_stub else "pending",
        attempts=0,
        next_attempt_at=None if auto_publish_stub else now,
        delivery_log=json.dumps(
            [
                {
                    "at": now.isoformat(),
                    "ok": True,
                    "channel": "dev_stub",
                    "message": "Development mode with no webhook configured",
                }
            ]
        )
        if auto_publish_stub
        else "[]",
    )
    db.add(event)
    return event
