"""Drain unpublished outbox events — signed webhook delivery to Nest."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import OutboxEvent

logger = logging.getLogger("control.outbox_worker")
settings = get_settings()
TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# Give up after this many failures and move the row to dead_letter so it stops
# consuming batch capacity. Operators can requeue it from the console.
MAX_ATTEMPTS = 8
BACKOFF_BASE_SECONDS = 30
BACKOFF_CAP_SECONDS = 3600


def outbox_worker_enabled() -> bool:
    """Whether this process should run the drain loop."""
    if not settings.workers_enabled:
        return False
    if settings.environment != "production":
        return True
    # In production, multiple replicas would each drain the queue. Redis presence
    # is the signal that coordination exists; a single-replica deployment can set
    # WORKERS_ENABLED explicitly.
    return bool(settings.redis_url.strip())


def _backoff_seconds(attempts: int) -> int:
    return min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)))


def _append_delivery_log(event: OutboxEvent, *, ok: bool, channel: str, message: str) -> None:
    try:
        log = json.loads(event.delivery_log or "[]")
    except json.JSONDecodeError:
        log = []
    log.append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "ok": ok,
            "channel": channel,
            "message": message,
        }
    )
    event.delivery_log = json.dumps(log[-20:])


def _sign(body: bytes, secret: str, timestamp: str) -> str:
    """HMAC over timestamp + body so a captured request cannot be replayed
    indefinitely and the payload cannot be tampered with in transit."""
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return mac.hexdigest()


def _mark_failed(event: OutboxEvent, message: str, channel: str = "webhook") -> None:
    event.attempts = int(event.attempts or 0) + 1
    event.last_error = message[:1000]
    _append_delivery_log(event, ok=False, channel=channel, message=message)
    if event.attempts >= MAX_ATTEMPTS:
        event.delivery_status = "dead_letter"
        event.next_attempt_at = None
        logger.error(
            "Outbox event %s (%s) dead-lettered after %s attempts: %s",
            event.id,
            event.event_type,
            event.attempts,
            message,
        )
    else:
        delay = _backoff_seconds(event.attempts)
        event.delivery_status = "pending"
        event.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)


def _mark_delivered(event: OutboxEvent, *, channel: str, message: str) -> None:
    event.published = True
    event.published_at = datetime.now(timezone.utc)
    event.delivery_status = "delivered"
    event.next_attempt_at = None
    event.last_error = ""
    _append_delivery_log(event, ok=True, channel=channel, message=message)


def process_outbox_batch(db: Session, *, limit: int = 50) -> dict:
    """Process due unpublished outbox rows. Caller should commit."""
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(OutboxEvent)
        .where(
            OutboxEvent.published.is_(False),
            OutboxEvent.delivery_status != "dead_letter",
            or_(OutboxEvent.next_attempt_at.is_(None), OutboxEvent.next_attempt_at <= now),
        )
        .order_by(OutboxEvent.id)
        .limit(limit)
    ).scalars().all()

    processed = 0
    published = 0
    failed = 0
    webhook_url = (settings.nest_config_webhook_url or "").strip()
    secret = (settings.nest_webhook_secret or "").strip()

    if not rows:
        return {"processed": 0, "published": 0, "failed": 0, "webhookConfigured": bool(webhook_url)}

    if not webhook_url:
        # No delivery channel. Leave rows pending rather than fabricating success,
        # so the backlog is visible in the console and drains once configured.
        logger.warning(
            "%s outbox events pending but NEST_CONFIG_WEBHOOK_URL is unset", len(rows)
        )
        return {
            "processed": 0,
            "published": 0,
            "failed": 0,
            "pending": len(rows),
            "webhookConfigured": False,
        }

    # One client for the batch — the old code opened a fresh connection pool per event.
    with httpx.Client(timeout=TIMEOUT) as client:
        for event in rows:
            processed += 1
            try:
                payload = json.loads(event.payload or "{}")
            except json.JSONDecodeError:
                payload = {}

            body = json.dumps(
                {
                    "eventType": event.event_type,
                    "aggregateType": event.aggregate_type,
                    "aggregateId": event.aggregate_id,
                    "payload": payload,
                    "eventId": event.id,
                },
                separators=(",", ":"),
            ).encode()

            timestamp = str(int(datetime.now(timezone.utc).timestamp()))
            headers = {
                "Content-Type": "application/json",
                "X-Control-Timestamp": timestamp,
                "X-Control-Event-Id": str(event.id),
            }
            if secret:
                headers["X-Control-Webhook-Secret"] = secret
                headers["X-Control-Signature"] = _sign(body, secret, timestamp)

            try:
                res = client.post(webhook_url, content=body, headers=headers)
            except httpx.HTTPError as exc:
                _mark_failed(event, f"transport: {exc}")
                failed += 1
                continue

            if res.status_code < 300:
                _mark_delivered(event, channel="webhook", message=f"HTTP {res.status_code}")
                published += 1
            elif res.status_code in (400, 401, 403, 422):
                # Rejected on content or auth — retrying identical bytes will not help.
                _mark_failed(event, f"HTTP {res.status_code}: {res.text[:200]}")
                event.attempts = MAX_ATTEMPTS
                event.delivery_status = "dead_letter"
                event.next_attempt_at = None
                failed += 1
            else:
                _mark_failed(event, f"HTTP {res.status_code}: {res.text[:200]}")
                failed += 1

    return {
        "processed": processed,
        "published": published,
        "failed": failed,
        "webhookConfigured": True,
    }


async def outbox_worker_loop(stop_event: asyncio.Event) -> None:
    """Background loop — runs while stop_event is not set."""
    interval = max(5, settings.outbox_worker_interval_seconds)
    logger.info(
        "Outbox worker started (interval=%ss, webhook=%s)",
        interval,
        bool(settings.nest_config_webhook_url),
    )
    while not stop_event.is_set():
        db = SessionLocal()
        try:
            # Delivery is blocking I/O; keep it off the event loop.
            result = await asyncio.to_thread(process_outbox_batch, db)
            if result["processed"]:
                db.commit()
                logger.info("Outbox batch: %s", result)
            else:
                db.rollback()
        except Exception:
            logger.exception("Outbox worker batch failed")
            db.rollback()
        finally:
            db.close()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
    logger.info("Outbox worker stopped")
