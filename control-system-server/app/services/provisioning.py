"""Control-plane contract for school-runtime initialization."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Tenant, TenantProvisioningJob
from app.services.outbox import enqueue_outbox
from app.services.trials import initialize_trial_once

DEFAULTS_VERSION = "v1"
INITIALIZATION_DEFAULTS = {
    "classLevels": {
        "nursery": ["Nursery 1", "Nursery 2"],
        "primary": [f"Primary {index}" for index in range(1, 7)],
        "jss": [f"JSS {index}" for index in range(1, 4)],
        "sss": [f"SSS {index}" for index in range(1, 4)],
    },
    "secondarySubjectCategories": ["science", "art", "commercial"],
}


def job_payload(tenant: Tenant, job: TenantProvisioningJob) -> dict:
    return {
        "schemaVersion": 1,
        "event": "tenant.initialization.requested",
        "jobId": job.id,
        "tenantId": tenant.external_id,
        "tenantSlug": tenant.slug,
        "defaultsVersion": job.defaults_version,
        "initializationDefaults": INITIALIZATION_DEFAULTS,
        "controlPlane": {
            "status": job.status,
            "schoolDbStatus": job.school_db_status,
            "schoolDbProvisioned": False,
        },
    }


def queue_initialization(
    db: Session,
    *,
    tenant: Tenant,
    requested_by: str,
    requested_by_id: str,
    idempotency_key: str,
) -> tuple[TenantProvisioningJob, bool]:
    existing = db.execute(
        select(TenantProvisioningJob).where(
            TenantProvisioningJob.tenant_id == tenant.id,
            TenantProvisioningJob.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    job = TenantProvisioningJob(
        tenant_id=tenant.id,
        status="queued",
        school_db_status="pending",
        idempotency_key=idempotency_key,
        defaults_version=DEFAULTS_VERSION,
        requested_by=requested_by,
        requested_by_id=requested_by_id,
    )
    db.add(job)
    db.flush()
    event = enqueue_outbox(
        db,
        event_type="tenant.initialization.requested",
        aggregate_type="tenant_provisioning_job",
        aggregate_id=str(job.id),
        payload=job_payload(tenant, job),
        # A development auto-publish records delivery only; it does not alter
        # the job or school DB status.
    )
    job.result = {"eventId": event.id, "schoolDbProvisioned": False}
    tenant.status = "provisioning"
    tenant.status_message = "School database initialization is pending."
    tenant.status_reason = "provisioning"
    return job, True


def apply_initialization_result(
    db: Session,
    *,
    tenant: Tenant,
    job: TenantProvisioningJob,
    status: str,
    school_db_status: str,
    error: str = "",
    result: dict | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    job.status = status
    job.school_db_status = school_db_status
    job.error = error[:4000]
    job.result = result or {}
    if status in ("succeeded", "failed"):
        job.completed_at = now
    if status == "succeeded" and school_db_status == "ready":
        tenant.status = "active"
        tenant.status_message = ""
        tenant.status_reason = ""
        initialize_trial_once(db, tenant=tenant)
    elif status == "failed":
        tenant.status = "provisioning"
        tenant.status_message = "School database initialization failed."
        tenant.status_reason = "provisioning"

