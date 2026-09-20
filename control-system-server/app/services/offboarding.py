"""Tenant offboarding: export → wipe schedule → wipe dispatch.

The control plane owns the schedule and the audit trail; the actual data deletion
happens in the school (Nest) server, which owns the per-tenant databases. Wipes
are dispatched as outbox events so the school server performs them and the
control plane records the outcome.

Before this existed, scheduling an offboard wrote a job row that nothing ever
read, so the wipe date passed silently and tenant data was retained indefinitely
despite the NDPR commitment recorded in the audit log.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import bump_config_version, record_audit
from app.models import Tenant, TenantOffboardJob
from app.services.outbox import enqueue_outbox

logger = logging.getLogger("control.offboarding")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def schedule_offboard_job(
    db: Session,
    *,
    tenant: Tenant,
    wipe_in_days: int = 30,
    notes: str = "",
    created_by: str = "system",
) -> TenantOffboardJob:
    """Create (or return) the active offboard job for a tenant."""
    existing = db.execute(
        select(TenantOffboardJob).where(
            TenantOffboardJob.tenant_id == tenant.id,
            TenantOffboardJob.status.in_(("scheduled", "export_pending", "wipe_scheduled")),
        )
    ).scalars().first()
    if existing is not None:
        return existing

    wipe_at = _now() + timedelta(days=wipe_in_days)
    job = TenantOffboardJob(
        tenant_id=tenant.id,
        status="export_pending",
        export_package_uri=f"stub://exports/tenant-{tenant.slug}-{tenant.id}.zip",
        wipe_scheduled_at=wipe_at,
        notes=notes,
        created_by=created_by,
    )
    db.add(job)
    db.flush()

    tenant.status = "archived"
    tenant.status_message = f"Offboarding scheduled — data wipe on {wipe_at.date().isoformat()}"
    tenant.status_reason = "offboarding"

    # Tell the school server to produce the export package.
    enqueue_outbox(
        db,
        event_type="tenant.offboard.export_requested",
        aggregate_type="tenant",
        aggregate_id=str(tenant.id),
        payload={
            "tenantId": tenant.id,
            "tenantRef": tenant.external_id,
            "slug": tenant.slug,
            "jobId": job.id,
            "wipeScheduledAt": wipe_at.isoformat(),
        },
    )
    bump_config_version(db)
    return job


def cancel_offboard_job(
    db: Session, job: TenantOffboardJob, *, reason: str, staff_email: str
) -> TenantOffboardJob:
    """Abort an offboard before the wipe fires and restore the tenant."""
    if job.status in ("completed", "cancelled"):
        return job
    job.status = "cancelled"
    job.notes = f"{job.notes}\nCancelled: {reason}".strip()

    tenant = db.get(Tenant, job.tenant_id)
    if tenant is not None and tenant.status_reason == "offboarding":
        tenant.status = "active"
        tenant.status_message = ""
        tenant.status_reason = ""
        bump_config_version(db)

    enqueue_outbox(
        db,
        event_type="tenant.offboard.cancelled",
        aggregate_type="tenant",
        aggregate_id=str(job.tenant_id),
        payload={"tenantId": job.tenant_id, "jobId": job.id, "reason": reason},
    )
    record_audit(
        db,
        action="tenant.offboard.cancel",
        actor_email=staff_email,
        target_type="tenant",
        target_id=str(job.tenant_id),
        reason=reason,
        after={"jobId": job.id},
    )
    return job


def dispatch_due_wipes(db: Session, *, limit: int = 20) -> list[dict]:
    """Dispatch wipes whose scheduled time has arrived.

    Legal hold is re-checked here, not just at scheduling time — a hold placed
    during the retention window must stop the wipe.
    """
    now = _now()
    jobs = db.execute(
        select(TenantOffboardJob)
        .where(
            # Never dispatch a destructive wipe until the school server has
            # acknowledged that the export package is ready.
            TenantOffboardJob.status == "wipe_scheduled",
            TenantOffboardJob.wipe_scheduled_at.isnot(None),
            TenantOffboardJob.wipe_scheduled_at <= now,
        )
        .order_by(TenantOffboardJob.id)
        .limit(limit)
    ).scalars().all()

    results: list[dict] = []
    for job in jobs:
        tenant = db.get(Tenant, job.tenant_id)
        if tenant is None:
            job.status = "cancelled"
            job.notes = f"{job.notes}\nTenant record no longer exists.".strip()
            results.append({"jobId": job.id, "outcome": "tenant_missing"})
            continue

        if tenant.legal_hold:
            job.status = "blocked_legal_hold"
            job.legal_hold_blocked = True
            job.notes = (
                f"{job.notes}\nWipe blocked at dispatch by legal hold: "
                f"{tenant.legal_hold_reason or 'no reason given'}"
            ).strip()
            record_audit(
                db,
                action="tenant.offboard.wipe_blocked",
                actor_email="system:offboarding",
                target_type="tenant",
                target_id=str(tenant.id),
                reason="legal_hold",
                after={"jobId": job.id},
            )
            results.append({"jobId": job.id, "outcome": "blocked_legal_hold"})
            continue

        job.status = "wipe_dispatched"
        enqueue_outbox(
            db,
            event_type="tenant.offboard.wipe_requested",
            aggregate_type="tenant",
            aggregate_id=str(tenant.id),
            payload={
                "tenantId": tenant.id,
                "tenantRef": tenant.external_id,
                "slug": tenant.slug,
                "jobId": job.id,
                "scheduledAt": job.wipe_scheduled_at.isoformat()
                if job.wipe_scheduled_at
                else None,
            },
        )
        record_audit(
            db,
            action="tenant.offboard.wipe_dispatched",
            actor_email="system:offboarding",
            target_type="tenant",
            target_id=str(tenant.id),
            after={"jobId": job.id},
        )
        results.append({"jobId": job.id, "outcome": "wipe_dispatched"})
        logger.info("Dispatched wipe for tenant %s (job %s)", tenant.id, job.id)

    return results


def complete_offboard_job(
    db: Session, job: TenantOffboardJob, *, ok: bool, detail: str = ""
) -> TenantOffboardJob:
    """Record the school server's confirmation that the wipe finished."""
    job.status = "completed" if ok else "failed"
    if detail:
        job.notes = f"{job.notes}\n{detail}".strip()
    record_audit(
        db,
        action="tenant.offboard.completed" if ok else "tenant.offboard.failed",
        actor_email="system:offboarding",
        target_type="tenant",
        target_id=str(job.tenant_id),
        after={"jobId": job.id, "detail": detail},
    )
    return job
