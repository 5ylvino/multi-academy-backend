"""Trial lifecycle, install fingerprints, and runtime enforcement."""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Subscription, Tenant, TenantInstall, TenantTrial
from app.audit import bump_config_version
from app.services.outbox import enqueue_outbox

DEFAULT_TRIAL_DAYS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.strip().encode()).hexdigest()


def issue_install_token() -> str:
    return secrets.token_urlsafe(32)


def install_matches(install: TenantInstall, token: str, device_hash: str) -> bool:
    return (
        install.revoked_at is None
        and secrets.compare_digest(install.install_token_hash, fingerprint(token))
        and (
            not install.device_hash
            or secrets.compare_digest(install.device_hash, fingerprint(device_hash))
            or secrets.compare_digest(install.device_hash, device_hash.strip())
        )
    )


def ensure_install(
    db: Session, *, tenant: Tenant, token: str | None = None, device_hash: str = ""
) -> tuple[TenantInstall, str | None]:
    row = db.execute(
        select(TenantInstall).where(TenantInstall.tenant_id == tenant.id)
    ).scalar_one_or_none()
    if row is not None:
        return row, None
    issued = token or issue_install_token()
    row = TenantInstall(
        tenant_id=tenant.id,
        install_token_hash=fingerprint(issued),
        device_hash=fingerprint(device_hash) if device_hash else "",
    )
    db.add(row)
    db.flush()
    return row, issued


def initialize_trial_once(
    db: Session, *, tenant: Tenant, started_at: datetime | None = None
) -> TenantTrial:
    existing = db.execute(
        select(TenantTrial).where(TenantTrial.tenant_id == tenant.id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    start = started_at or _now()
    trial = TenantTrial(
        tenant_id=tenant.id,
        duration_days=DEFAULT_TRIAL_DAYS,
        enabled=True,
        status="active",
        started_at=start,
        ends_at=start + timedelta(days=DEFAULT_TRIAL_DAYS),
    )
    db.add(trial)
    db.flush()
    enqueue_outbox(
        db,
        event_type="tenant.trial.started",
        aggregate_type="tenant",
        aggregate_id=tenant.external_id,
        payload={
            "tenantId": tenant.external_id,
            "startedAt": trial.started_at.isoformat(),
            "endsAt": trial.ends_at.isoformat(),
            "durationDays": trial.duration_days,
        },
    )
    return trial


def trial_metadata(db: Session, tenant: Tenant) -> dict | None:
    trial = db.execute(
        select(TenantTrial).where(TenantTrial.tenant_id == tenant.id)
    ).scalar_one_or_none()
    if trial is None:
        return None
    now = _now()
    ends = trial.ends_at.replace(tzinfo=timezone.utc) if trial.ends_at.tzinfo is None else trial.ends_at
    expired = trial.enabled and trial.status == "active" and ends <= now
    return {
        "enabled": trial.enabled,
        "status": "expired" if expired else trial.status,
        "durationDays": trial.duration_days,
        "startedAt": trial.started_at.isoformat() if trial.started_at else None,
        "endsAt": trial.ends_at.isoformat() if trial.ends_at else None,
        "daysRemaining": max(0, (ends - now).days) if trial.enabled and not expired else 0,
        "expired": expired or trial.status == "expired",
    }


def run_trial_pass(db: Session) -> dict[str, int]:
    """Send seven/one-day notices and expire due grants idempotently."""
    now = _now()
    rows = db.execute(select(TenantTrial).where(TenantTrial.enabled.is_(True))).scalars().all()
    reminders = expired = 0
    for trial in rows:
        ends = trial.ends_at.replace(tzinfo=timezone.utc) if trial.ends_at.tzinfo is None else trial.ends_at
        tenant = db.get(Tenant, trial.tenant_id)
        if tenant is None:
            continue
        if trial.status == "active" and ends <= now:
            trial.status = "expired"
            trial.expired_at = now
            subscription = db.execute(
                select(Subscription).where(
                    Subscription.tenant_id == tenant.id,
                    Subscription.is_current.is_(True),
                    Subscription.status == "trialing",
                )
            ).scalars().first()
            if subscription is not None:
                subscription.status = "cancelled"
                subscription.notes = (
                    f"{subscription.notes} Trial expired at {now.isoformat()}".strip()
                )
            if tenant.status == "active":
                tenant.status = "restricted"
                tenant.status_reason = "trial_expired"
                tenant.status_message = "Your trial has expired. Please contact support to continue."
                bump_config_version(db)
            enqueue_outbox(
                db, event_type="trial.notification", aggregate_type="tenant",
                aggregate_id=tenant.external_id,
                payload={
                    "tenantId": tenant.external_id,
                    "adminEmail": tenant.admin_email,
                    "title": "Your school trial has expired",
                    "message": "Your 90-day free trial has ended. Please contact support or activate a valid subscription to restore access.",
                    "endsAt": ends.isoformat(),
                },
            )
            enqueue_outbox(
                db,
                event_type="config.invalidate",
                aggregate_type="tenant",
                aggregate_id=tenant.external_id,
                payload={"tenantId": tenant.external_id, "reason": "trial_expired"},
            )
            expired += 1
            continue
        for days, field in ((7, "reminder_7_sent_at"), (1, "reminder_1_sent_at")):
            if trial.status != "active" or getattr(trial, field) is not None:
                continue
            if now >= ends - timedelta(days=days):
                setattr(trial, field, now)
                enqueue_outbox(
                    db, event_type="trial.notification", aggregate_type="tenant",
                    aggregate_id=tenant.external_id,
                    payload={
                        "tenantId": tenant.external_id,
                        "adminEmail": tenant.admin_email,
                        "title": f"Your school trial expires in {days} day{'s' if days != 1 else ''}",
                        "message": f"Your school trial expires on {ends.date().isoformat()}. Please activate a subscription before then to avoid interruption.",
                        "daysRemaining": days,
                        "endsAt": ends.isoformat(),
                    },
                )
                reminders += 1
    return {"reminders": reminders, "expired": expired}


def run_unsubscribed_pass(
    db: Session, *, grace_days: int = 7
) -> int:
    """Suspend active tenants that have neither a trial nor a valid subscription."""
    now = _now()
    cutoff = now - timedelta(days=max(1, grace_days))
    tenants = db.execute(
        select(Tenant).where(Tenant.status == "active", Tenant.created_at <= cutoff)
    ).scalars().all()
    suspended = 0
    for tenant in tenants:
        trial = db.execute(
            select(TenantTrial).where(TenantTrial.tenant_id == tenant.id)
        ).scalar_one_or_none()
        if trial is not None and trial.enabled and trial.status == "active":
            continue
        valid = db.execute(
            select(Subscription).where(
                Subscription.tenant_id == tenant.id,
                Subscription.is_current.is_(True),
                Subscription.status.in_(("active", "trialing")),
            )
        ).scalars().first()
        if valid is not None:
            continue
        tenant.status = "suspended"
        tenant.status_reason = "billing_grace_expired"
        tenant.status_message = (
            "This school is suspended because it has no active subscription."
        )
        bump_config_version(db)
        enqueue_outbox(
            db,
            event_type="billing.notification",
            aggregate_type="tenant",
            aggregate_id=tenant.external_id,
            payload={
                "tenantId": tenant.external_id,
                "adminEmail": tenant.admin_email,
                "title": "School account suspended",
                "message": "Your school account has been suspended because the grace period ended without an active subscription.",
            },
        )
        enqueue_outbox(
            db,
            event_type="config.invalidate",
            aggregate_type="tenant",
            aggregate_id=tenant.external_id,
            payload={"tenantId": tenant.external_id, "reason": "billing_grace_expired"},
        )
        suspended += 1
    return suspended
