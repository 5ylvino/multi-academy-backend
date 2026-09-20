import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import bump_config_version, record_audit
from app.database import get_db
from app.deps import StaffContext, client_ip, require_permissions
from app.models import (
    Plan,
    SchoolBlacklistEntry,
    Subscription,
    Tenant,
    TenantDomain,
    TenantProvisioningJob,
    TenantTrial,
    TenantOffboardJob,
)
from app.models.tenant import TENANT_STATUSES
from app.schemas import (
    BlacklistRequest,
    OffboardCreate,
    OffboardJobOut,
    SubscriptionCreate,
    SubscriptionOut,
    TenantCreate,
    TenantDomainCreate,
    TenantDomainOut,
    TenantOut,
    TenantStatusChange,
    TenantUpdate,
    ProvisioningJobOut,
    ProvisioningRequest,
    TrialOut,
    TrialUpdate,
)
from app.services.dual_control import create_request, dual_control_enabled
from app.services.offboarding import cancel_offboard_job, schedule_offboard_job
from app.services.runtime_config import build_runtime_config
from app.services.provisioning import queue_initialization
from app.services.trials import initialize_trial_once, trial_metadata
from app.services.outbox import enqueue_outbox

router = APIRouter(prefix="/v1/tenants", tags=["tenants"])
_HOSTNAME_RE = re.compile(r"^(?=.{1,255}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _get_tenant(db: Session, tenant_id: int) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    return tenant


@router.get("", response_model=list[TenantOut])
def list_tenants(
    q: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    _: StaffContext = Depends(require_permissions("tenants:read")),
    db: Session = Depends(get_db),
):
    stmt = select(Tenant).order_by(Tenant.created_at.desc())
    if status_filter:
        stmt = stmt.where(Tenant.status == status_filter)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            Tenant.name.ilike(like) | Tenant.slug.ilike(like) | Tenant.admin_email.ilike(like)
        )
    tenants = db.execute(stmt).scalars().all()
    result = []
    for tenant in tenants:
        row = TenantOut.model_validate(tenant).model_dump()
        trial = trial_metadata(db, tenant)
        if trial:
            row["trial_status"] = trial["status"]
            row["trial_ends_at"] = trial["endsAt"]
            row["trial_days_remaining"] = trial["daysRemaining"]
        result.append(row)
    return result


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
def create_tenant(
    body: TenantCreate,
    staff: StaffContext = Depends(require_permissions("tenants:write")),
    db: Session = Depends(get_db),
):
    normalized_slug = body.slug.strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", normalized_slug):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "slug must be a lowercase DNS label")
    blacklist = db.execute(
        select(SchoolBlacklistEntry).where(
            SchoolBlacklistEntry.is_active.is_(True),
            (
                SchoolBlacklistEntry.slug == normalized_slug
                if normalized_slug
                else SchoolBlacklistEntry.slug == ""
            ),
        )
    ).scalars().first()
    if blacklist is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is blocked for this school")

    exists = db.execute(
        select(Tenant).where(
            (Tenant.slug == normalized_slug) | (Tenant.external_id == body.external_id)
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Tenant slug or external id already exists")

    tenant = Tenant(
        external_id=body.external_id,
        slug=normalized_slug,
        name=body.name,
        region=body.region,
        residency_tag=body.residency_tag or body.region or "NG",
        admin_email=body.admin_email,
        admin_phone=body.admin_phone,
        status="active",
    )
    db.add(tenant)
    db.flush()

    if body.plan_key:
        plan = db.execute(select(Plan).where(Plan.key == body.plan_key)).scalar_one_or_none()
        if plan is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown plan: {body.plan_key}")
        db.add(Subscription(tenant_id=tenant.id, type="catalog", plan_id=plan.id))

    record_audit(
        db,
        action="tenant.create",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="tenant",
        target_id=body.slug,
        after={"name": body.name, "plan": body.plan_key},
    )
    bump_config_version(db)
    db.commit()
    db.refresh(tenant)
    return tenant


@router.get("/{tenant_id}", response_model=TenantOut)
def get_tenant(
    tenant_id: int,
    _: StaffContext = Depends(require_permissions("tenants:read")),
    db: Session = Depends(get_db),
):
    return _get_tenant(db, tenant_id)


@router.patch("/{tenant_id}", response_model=TenantOut)
def update_tenant(
    tenant_id: int,
    body: TenantUpdate,
    staff: StaffContext = Depends(require_permissions("tenants:write")),
    db: Session = Depends(get_db),
):
    tenant = _get_tenant(db, tenant_id)
    before = {
        "name": tenant.name,
        "notes": tenant.notes,
        "tags": tenant.tags,
        "residency_tag": tenant.residency_tag,
        "legal_hold": tenant.legal_hold,
    }
    for field in (
        "name",
        "region",
        "residency_tag",
        "legal_hold",
        "legal_hold_reason",
        "admin_email",
        "admin_phone",
        "tags",
        "notes",
    ):
        value = getattr(body, field)
        if value is not None:
            setattr(tenant, field, value)
    record_audit(
        db,
        action="tenant.update",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="tenant",
        target_id=str(tenant_id),
        before=before,
        after={
            "name": tenant.name,
            "notes": tenant.notes,
            "tags": tenant.tags,
            "residency_tag": tenant.residency_tag,
            "legal_hold": tenant.legal_hold,
        },
    )
    db.commit()
    db.refresh(tenant)
    return tenant


@router.post("/{tenant_id}/status", response_model=TenantOut)
def change_status(
    tenant_id: int,
    body: TenantStatusChange,
    request: Request,
    staff: StaffContext = Depends(require_permissions("tenants:write")),
    db: Session = Depends(get_db),
):
    if body.status not in TENANT_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown status: {body.status}")
    if body.status == "blacklisted":
        # Blacklisting must go through the dedicated endpoint (fingerprints + stricter permission).
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Use POST /{id}/blacklist")
    if not body.reason:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reason required for status changes")

    tenant = _get_tenant(db, tenant_id)
    before = tenant.status
    tenant.status = body.status
    tenant.status_message = body.message
    tenant.status_reason = body.reason[:32]

    record_audit(
        db,
        action="tenant.status_change",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="tenant",
        target_id=str(tenant_id),
        reason=body.reason,
        before={"status": before},
        after={"status": body.status},
        ip_address=client_ip(request),
    )
    bump_config_version(db)
    db.commit()
    db.refresh(tenant)
    return tenant


@router.get("/{tenant_id}/domains", response_model=list[TenantDomainOut])
def list_tenant_domains(
    tenant_id: int,
    _: StaffContext = Depends(require_permissions("tenants:read")),
    db: Session = Depends(get_db),
):
    _get_tenant(db, tenant_id)
    return db.execute(
        select(TenantDomain)
        .where(TenantDomain.tenant_id == tenant_id)
        .order_by(TenantDomain.is_primary.desc(), TenantDomain.id)
    ).scalars().all()


@router.post("/{tenant_id}/domains", response_model=TenantDomainOut, status_code=status.HTTP_201_CREATED)
def add_tenant_domain(
    tenant_id: int,
    body: TenantDomainCreate,
    staff: StaffContext = Depends(require_permissions("tenants:write")),
    db: Session = Depends(get_db),
):
    tenant = _get_tenant(db, tenant_id)
    hostname = body.hostname.strip().lower().rstrip(".")
    if not _HOSTNAME_RE.fullmatch(hostname):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "hostname must be a valid DNS name")
    if body.kind not in ("subdomain", "custom"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "kind must be subdomain or custom")
    if db.execute(select(TenantDomain).where(TenantDomain.hostname == hostname)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Hostname is already assigned")
    is_primary = db.execute(
        select(TenantDomain).where(TenantDomain.tenant_id == tenant.id, TenantDomain.is_primary.is_(True))
    ).scalar_one_or_none() is None
    domain = TenantDomain(
        tenant_id=tenant.id,
        hostname=hostname,
        kind=body.kind,
        status="active" if body.kind == "subdomain" else "pending",
        ssl_status="pending" if body.kind == "custom" else "active",
        is_primary=is_primary,
    )
    db.add(domain)
    record_audit(
        db,
        action="tenant.domain.add",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="tenant_domain",
        target_id=hostname,
        after={"tenant_id": tenant.id, "kind": body.kind, "is_primary": is_primary},
    )
    db.commit()
    db.refresh(domain)
    return domain


@router.post("/{tenant_id}/blacklist", response_model=TenantOut)
def blacklist_tenant(
    tenant_id: int,
    body: BlacklistRequest,
    request: Request,
    staff: StaffContext = Depends(require_permissions("tenants:blacklist")),
    db: Session = Depends(get_db),
):
    tenant = _get_tenant(db, tenant_id)
    if dual_control_enabled():
        req = create_request(
            db,
            action="tenant.blacklist",
            target_type="tenant",
            target_id=str(tenant_id),
            payload={
                "evidence": body.evidence,
                "severity": body.severity,
                "permanent": body.permanent,
            },
            reason=body.reason,
            staff_id=staff.id,
            staff_email=staff.email,
        )
        db.commit()
        raise HTTPException(
            status.HTTP_202_ACCEPTED,
            f"Dual-control required for blacklist. Pending approval id={req.id}",
        )
    before = tenant.status
    tenant.status = "blacklisted"
    tenant.status_message = "This school has been blocked from the platform. Contact support."

    # Cancel SaaS billing + record identity fingerprints for the registration gate.
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
            reason=body.reason,
            evidence=body.evidence,
            severity=body.severity,
            permanent=body.permanent,
            review_by=body.review_by,
            created_by=staff.email,
        )
    )

    record_audit(
        db,
        action="tenant.blacklist",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="tenant",
        target_id=str(tenant_id),
        reason=body.reason,
        before={"status": before},
        after={"status": "blacklisted"},
        ip_address=client_ip(request),
    )
    bump_config_version(db)
    db.commit()
    db.refresh(tenant)
    return tenant


@router.post("/{tenant_id}/unblacklist", response_model=TenantOut)
def unblacklist_tenant(
    tenant_id: int,
    body: TenantStatusChange,
    request: Request,
    staff: StaffContext = Depends(require_permissions("tenants:blacklist")),
    db: Session = Depends(get_db),
):
    if not body.reason:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reason required")
    tenant = _get_tenant(db, tenant_id)
    if dual_control_enabled():
        req = create_request(
            db,
            action="tenant.unblacklist",
            target_type="tenant",
            target_id=str(tenant_id),
            payload={},
            reason=body.reason,
            staff_id=staff.id,
            staff_email=staff.email,
        )
        db.commit()
        raise HTTPException(
            status.HTTP_202_ACCEPTED,
            f"Dual-control required for un-blacklist. Pending approval id={req.id}",
        )
    tenant.status = "active"
    tenant.status_message = ""
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
        reason=body.reason,
        before={"status": "blacklisted"},
        after={"status": "active"},
        ip_address=client_ip(request),
    )
    bump_config_version(db)
    db.commit()
    db.refresh(tenant)
    return tenant


@router.post(
    "/{tenant_id}/offboard",
    response_model=OffboardJobOut,
    status_code=status.HTTP_201_CREATED,
)
def schedule_offboard(
    tenant_id: int,
    body: OffboardCreate,
    staff: StaffContext = Depends(require_permissions("tenants:write")),
    db: Session = Depends(get_db),
):
    """Schedule tenant offboarding: export package, then a scheduled data wipe."""
    if not (body.reason or "").strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Reason is required to offboard a tenant"
        )
    tenant = _get_tenant(db, tenant_id)

    if tenant.legal_hold:
        job = TenantOffboardJob(
            tenant_id=tenant.id,
            status="blocked_legal_hold",
            legal_hold_blocked=True,
            notes=f"Legal hold: {tenant.legal_hold_reason or 'no reason'}. {body.notes}",
            created_by=staff.email,
        )
        db.add(job)
        record_audit(
            db,
            action="tenant.offboard.blocked",
            actor_id=staff.id,
            actor_email=staff.email,
            target_type="tenant",
            target_id=str(tenant_id),
            reason=body.reason or "legal_hold",
        )
        db.commit()
        db.refresh(job)
        return job

    # Offboarding schedules irreversible data destruction, so it needs the same
    # two-person approval as blacklisting. It previously required only
    # `tenants:write` even though the console advertised it as dual-controlled.
    if dual_control_enabled():
        req = create_request(
            db,
            action="tenant.offboard",
            target_type="tenant",
            target_id=str(tenant_id),
            payload={"wipe_in_days": body.wipe_in_days, "notes": body.notes},
            reason=body.reason,
            staff_id=staff.id,
            staff_email=staff.email,
        )
        db.commit()
        raise HTTPException(
            status.HTTP_202_ACCEPTED,
            f"Dual-control required to offboard a tenant. Pending approval id={req.id}",
        )

    job = schedule_offboard_job(
        db,
        tenant=tenant,
        wipe_in_days=body.wipe_in_days,
        notes=body.notes,
        created_by=staff.email,
    )
    record_audit(
        db,
        action="tenant.offboard.schedule",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="tenant",
        target_id=str(tenant_id),
        reason=body.reason,
        after={
            "status": tenant.status,
            "wipe_scheduled_at": job.wipe_scheduled_at.isoformat()
            if job.wipe_scheduled_at
            else None,
            "export_package_uri": job.export_package_uri,
        },
    )
    db.commit()
    db.refresh(job)
    return job


@router.post("/{tenant_id}/offboard/{job_id}/cancel", response_model=OffboardJobOut)
def cancel_offboard(
    tenant_id: int,
    job_id: int,
    body: TenantStatusChange,
    staff: StaffContext = Depends(require_permissions("tenants:write")),
    db: Session = Depends(get_db),
):
    """Abort a scheduled offboard before its wipe fires."""
    if not (body.reason or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reason required")
    _get_tenant(db, tenant_id)
    job = db.get(TenantOffboardJob, job_id)
    if job is None or job.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Offboard job not found")
    if job.status in ("completed", "wipe_dispatched"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Job is {job.status} — the wipe has already been dispatched and cannot be recalled",
        )
    cancel_offboard_job(db, job, reason=body.reason, staff_email=staff.email)
    db.commit()
    db.refresh(job)
    return job


@router.get("/{tenant_id}/offboard", response_model=list[OffboardJobOut])
def list_offboard_jobs(
    tenant_id: int,
    _: StaffContext = Depends(require_permissions("tenants:read")),
    db: Session = Depends(get_db),
):
    _get_tenant(db, tenant_id)
    return db.execute(
        select(TenantOffboardJob)
        .where(TenantOffboardJob.tenant_id == tenant_id)
        .order_by(TenantOffboardJob.id.desc())
    ).scalars().all()


@router.post("/{tenant_id}/provision")
def provision_tenant(
    tenant_id: int,
    body: ProvisioningRequest | None = None,
    staff: StaffContext = Depends(require_permissions("tenants:write")),
    db: Session = Depends(get_db),
):
    """Queue school-runtime initialization without claiming DB completion."""
    tenant = _get_tenant(db, tenant_id)
    if tenant.status not in ("provisioning", "active", "archived"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Cannot provision from status {tenant.status}",
        )
    idempotency_key = (
        body.idempotency_key if body and body.idempotency_key else uuid.uuid4().hex
    )
    before = tenant.status
    job, created = queue_initialization(
        db,
        tenant=tenant,
        requested_by=staff.email,
        requested_by_id=staff.id,
        idempotency_key=idempotency_key,
    )
    record_audit(
        db,
        action="tenant.provision.requested" if created else "tenant.provision.replayed",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="tenant",
        target_id=str(tenant_id),
        before={"status": before},
        after={
            "status": tenant.status,
            "jobId": job.id,
            "jobStatus": job.status,
            "schoolDbStatus": job.school_db_status,
            "schoolDbProvisioned": False,
        },
        reason="School runtime initialization requested",
    )
    bump_config_version(db)
    db.commit()
    db.refresh(job)
    return {
        # Keep the old top-level keys stable for existing console clients.
        "jobId": job.id,
        "status": job.status,
        "schoolDbStatus": job.school_db_status,
        "schoolDbProvisioned": False,
        "tenant": TenantOut.model_validate(tenant).model_dump(),
        "event": "tenant.initialization.requested",
        "eventId": job.result.get("eventId"),
        "defaultsVersion": job.defaults_version,
        "message": "Initialization queued; the school database is not provisioned yet.",
    }


@router.get("/{tenant_id}/provision/{job_id}", response_model=ProvisioningJobOut)
def get_provisioning_job(
    tenant_id: int,
    job_id: int,
    _: StaffContext = Depends(require_permissions("tenants:read")),
    db: Session = Depends(get_db),
):
    _get_tenant(db, tenant_id)
    job = db.get(TenantProvisioningJob, job_id)
    if job is None or job.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provisioning job not found")
    return job


@router.get("/{tenant_id}/effective-config")
def effective_config(
    tenant_id: int,
    _: StaffContext = Depends(require_permissions("tenants:read")),
    db: Session = Depends(get_db),
):
    """Preview: exactly what the school server would receive."""
    tenant = _get_tenant(db, tenant_id)
    return build_runtime_config(db, tenant)


@router.get("/{tenant_id}/trial", response_model=TrialOut)
def get_trial(
    tenant_id: int,
    _: StaffContext = Depends(require_permissions("tenants:read")),
    db: Session = Depends(get_db),
):
    _get_tenant(db, tenant_id)
    trial = db.execute(select(TenantTrial).where(TenantTrial.tenant_id == tenant_id)).scalar_one_or_none()
    if trial is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trial has not been initialized")
    return trial


@router.patch("/{tenant_id}/trial", response_model=TrialOut)
def update_trial(
    tenant_id: int,
    body: TrialUpdate,
    request: Request,
    staff: StaffContext = Depends(require_permissions("tenants:write")),
    db: Session = Depends(get_db),
):
    tenant = _get_tenant(db, tenant_id)
    trial = db.execute(select(TenantTrial).where(TenantTrial.tenant_id == tenant_id)).scalar_one_or_none()
    if trial is None:
        trial = initialize_trial_once(db, tenant=tenant)
    before = {
        "duration_days": trial.duration_days,
        "ends_at": trial.ends_at.isoformat(),
        "enabled": trial.enabled,
        "status": trial.status,
    }
    if body.duration_days is not None:
        trial.duration_days = body.duration_days
        trial.ends_at = trial.started_at + timedelta(days=body.duration_days)
    if body.ends_at is not None:
        trial.ends_at = body.ends_at
    if body.enabled is not None:
        trial.enabled = body.enabled
        ends = trial.ends_at
        if ends.tzinfo is None:
            ends = ends.replace(tzinfo=timezone.utc)
        if body.enabled:
            if ends > datetime.now(timezone.utc):
                trial.status = "active"
                trial_subscription = db.execute(
                    select(Subscription).where(
                        Subscription.tenant_id == tenant.id,
                        Subscription.is_current.is_(True),
                        Subscription.status == "cancelled",
                        Subscription.notes.ilike("%Automatic one-time%"),
                    )
                ).scalars().first()
                if trial_subscription is not None:
                    trial_subscription.status = "trialing"
                if tenant.status in ("restricted", "suspended") and tenant.status_reason in (
                    "trial_expired",
                    "trial_manual",
                ):
                    tenant.status = "active"
                    tenant.status_reason = ""
                    tenant.status_message = ""
        else:
            trial.status = "disabled"
            trial_subscription = db.execute(
                select(Subscription).where(
                    Subscription.tenant_id == tenant.id,
                    Subscription.is_current.is_(True),
                    Subscription.status == "trialing",
                    Subscription.notes.ilike("%Automatic one-time%"),
                )
            ).scalars().first()
            if trial_subscription is not None:
                trial_subscription.status = "cancelled"
            active_subscription = db.execute(
                select(Subscription).where(
                    Subscription.tenant_id == tenant.id,
                    Subscription.is_current.is_(True),
                    Subscription.status == "active",
                )
            ).scalars().first()
            if active_subscription is None:
                tenant.status = "restricted"
                tenant.status_reason = "trial_manual"
                tenant.status_message = "Trial access has been disabled by platform support."
    record_audit(
        db, action="tenant.trial.update", actor_id=staff.id, actor_email=staff.email,
        target_type="tenant_trial", target_id=str(trial.id), reason=body.reason,
        before=before, after={
            "duration_days": trial.duration_days,
            "ends_at": trial.ends_at.isoformat(),
            "enabled": trial.enabled,
            "status": trial.status,
        }, ip_address=client_ip(request),
    )
    bump_config_version(db)
    enqueue_outbox(
        db,
        event_type="config.invalidate",
        aggregate_type="tenant",
        aggregate_id=tenant.external_id,
        payload={"tenantId": tenant.external_id, "reason": "trial_update"},
    )
    db.commit()
    db.refresh(trial)
    return trial


# ------------------------------------------------------------- subscriptions

@router.get("/{tenant_id}/subscriptions", response_model=list[SubscriptionOut])
def list_subscriptions(
    tenant_id: int,
    _: StaffContext = Depends(require_permissions("subscriptions:read")),
    db: Session = Depends(get_db),
):
    _get_tenant(db, tenant_id)
    return db.execute(
        select(Subscription)
        .where(Subscription.tenant_id == tenant_id)
        .order_by(Subscription.id.desc())
    ).scalars().all()


@router.post(
    "/{tenant_id}/subscriptions",
    response_model=SubscriptionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_subscription(
    tenant_id: int,
    body: SubscriptionCreate,
    staff: StaffContext = Depends(require_permissions("subscriptions:write")),
    db: Session = Depends(get_db),
):
    tenant = _get_tenant(db, tenant_id)

    plan_id = None
    if body.type == "catalog":
        if not body.plan_key:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "plan_key required for catalog subscriptions")
        plan = db.execute(select(Plan).where(Plan.key == body.plan_key)).scalar_one_or_none()
        if plan is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown plan: {body.plan_key}")
        plan_id = plan.id
    elif body.type != "custom":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "type must be catalog or custom")

    # Retire previous subscription
    for sub in db.execute(
        select(Subscription).where(
            Subscription.tenant_id == tenant.id, Subscription.is_current.is_(True)
        )
    ).scalars():
        sub.is_current = False

    subscription = Subscription(
        tenant_id=tenant.id,
        type=body.type,
        plan_id=plan_id,
        billing_cycle=body.billing_cycle,
        price_minor=body.price_minor,
        currency=body.currency,
        custom_entitlements=body.custom_entitlements,
        custom_quotas=body.custom_quotas,
        contract_start=body.contract_start,
        contract_end=body.contract_end,
        notes=body.notes,
        deal_name=body.deal_name or "",
        is_current=True,
    )
    db.add(subscription)
    record_audit(
        db,
        action="subscription.create",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="tenant",
        target_id=str(tenant_id),
        after={"type": body.type, "plan": body.plan_key, "cycle": body.billing_cycle},
    )
    bump_config_version(db)
    db.commit()
    db.refresh(subscription)
    return subscription
