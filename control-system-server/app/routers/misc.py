import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import bump_config_version, current_config_version, record_audit
from app.database import get_db
from app.deps import StaffContext, require_permissions
from app.models import (
    AuditEvent,
    Block,
    MaintenanceWindow,
    OutboxEvent,
    ProviderConfig,
    SchoolBlacklistEntry,
    ServiceClient,
    Subscription,
    Tenant,
)
from app.schemas import (
    AuditOut,
    MaintenanceCreate,
    MaintenanceOut,
    ServiceClientCreate,
    ServiceClientCreated,
    ServiceClientOut,
)
from app.security import generate_client_secret, hash_client_secret
from app.services.billing import estimate_mrr, revenue_trajectory

router = APIRouter(prefix="/v1", tags=["platform"])


# ----------------------------------------------------------------- dashboard

@router.get("/dashboard")
def dashboard(
    _: StaffContext = Depends(require_permissions("dashboard:read")),
    db: Session = Depends(get_db),
):
    tenant_counts = dict(
        db.execute(select(Tenant.status, func.count()).group_by(Tenant.status)).all()
    )
    active_blocks = db.execute(
        select(func.count()).select_from(Block).where(Block.is_active.is_(True))
    ).scalar_one()
    blacklisted_schools = db.execute(
        select(func.count())
        .select_from(SchoolBlacklistEntry)
        .where(SchoolBlacklistEntry.is_active.is_(True))
    ).scalar_one()
    subscriptions = dict(
        db.execute(
            select(Subscription.type, func.count())
            .where(Subscription.is_current.is_(True))
            .group_by(Subscription.type)
        ).all()
    )
    recent_audit = db.execute(
        select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(15)
    ).scalars().all()
    provider_health = [
        {
            "id": p.id,
            "capability": p.capability,
            "providerId": p.provider_id,
            "mode": p.mode,
            "scope": "global" if p.tenant_id is None else f"tenant:{p.tenant_id}",
            "healthStatus": p.health_status,
            "enabled": p.is_enabled,
        }
        for p in db.execute(
            select(ProviderConfig)
            .where(ProviderConfig.tenant_id.is_(None))
            .order_by(ProviderConfig.capability)
        ).scalars().all()
    ]

    return {
        "tenants": {"byStatus": tenant_counts, "total": sum(tenant_counts.values())},
        "subscriptions": subscriptions,
        "activeBlocks": active_blocks,
        "blacklistedSchools": blacklisted_schools,
        "configVersion": current_config_version(db),
        "providerHealth": provider_health,
        "health": {"controlApi": "green", "database": "green"},
        "mrr": estimate_mrr(db),
        "trajectory": revenue_trajectory(db, months=12),
        "recentAudit": [AuditOut.model_validate(e).model_dump() for e in recent_audit],
    }


# --------------------------------------------------------------------- audit

@router.get("/audit", response_model=list[AuditOut])
def search_audit(
    action: str | None = Query(default=None),
    target_id: str | None = Query(default=None),
    actor_email: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    _: StaffContext = Depends(require_permissions("audit:read")),
    db: Session = Depends(get_db),
):
    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc())
    if action:
        stmt = stmt.where(AuditEvent.action.ilike(f"%{action}%"))
    if target_id:
        stmt = stmt.where(AuditEvent.target_id == target_id)
    if actor_email:
        stmt = stmt.where(AuditEvent.actor_email.ilike(f"%{actor_email}%"))
    return db.execute(stmt.limit(limit).offset(offset)).scalars().all()


# --------------------------------------------------------------- maintenance

@router.get("/maintenance", response_model=list[MaintenanceOut])
def list_maintenance(
    _: StaffContext = Depends(require_permissions("dashboard:read")),
    db: Session = Depends(get_db),
):
    return db.execute(
        select(MaintenanceWindow).order_by(MaintenanceWindow.starts_at.desc())
    ).scalars().all()


@router.post("/maintenance", response_model=MaintenanceOut, status_code=status.HTTP_201_CREATED)
def create_maintenance(
    body: MaintenanceCreate,
    staff: StaffContext = Depends(require_permissions("maintenance:write")),
    db: Session = Depends(get_db),
):
    window = MaintenanceWindow(
        message=body.message,
        severity=body.severity,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        tenant_ids=body.tenant_ids,
        created_by=staff.email,
    )
    db.add(window)
    record_audit(
        db,
        action="maintenance.create",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="maintenance",
        after={"message": body.message, "starts_at": body.starts_at.isoformat()},
    )
    bump_config_version(db)
    db.commit()
    db.refresh(window)
    return window


@router.delete("/maintenance/{window_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_maintenance(
    window_id: int,
    staff: StaffContext = Depends(require_permissions("maintenance:write")),
    db: Session = Depends(get_db),
):
    window = db.get(MaintenanceWindow, window_id)
    if window is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Window not found")
    window.is_active = False
    record_audit(
        db,
        action="maintenance.cancel",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="maintenance",
        target_id=str(window_id),
    )
    bump_config_version(db)
    db.commit()


# -------------------------------------------------------------------- jobs

@router.get("/jobs")
def list_jobs(
    _: StaffContext = Depends(require_permissions("dashboard:read")),
    db: Session = Depends(get_db),
):
    """Recent job-like activity from outbox + dunning hints."""
    from app.services.outbox_worker import outbox_worker_enabled

    recent_outbox = db.execute(
        select(OutboxEvent).order_by(OutboxEvent.id.desc()).limit(30)
    ).scalars().all()
    pending = sum(1 for e in recent_outbox if not e.published)

    jobs = []
    for e in recent_outbox[:20]:
        jobs.append(
            {
                "id": f"outbox-{e.id}",
                "type": "outbox",
                "name": e.event_type,
                "status": "completed" if e.published else "pending",
                "aggregateType": e.aggregate_type,
                "aggregateId": e.aggregate_id,
                "createdAt": e.created_at.isoformat() if e.created_at else None,
                "publishedAt": e.published_at.isoformat() if e.published_at else None,
            }
        )

    return {
        "jobs": jobs,
        "summary": {
            "outboxPending": pending,
            "outboxWorkerEnabled": outbox_worker_enabled(),
            "recentCount": len(jobs),
        },
        "actions": [
            {"method": "POST", "path": "/v1/jobs/outbox/run", "description": "Drain outbox batch"},
            {"method": "POST", "path": "/v1/billing/dunning/run", "description": "Run dunning pass"},
            {"method": "POST", "path": "/v1/billing/invoices/generate-due", "description": "Generate due invoices"},
        ],
    }


@router.post("/jobs/outbox/run")
def run_outbox_job(
    staff: StaffContext = Depends(require_permissions("dashboard:read")),
    db: Session = Depends(get_db),
):
    from app.services.outbox_worker import process_outbox_batch

    result = process_outbox_batch(db)
    record_audit(
        db,
        action="jobs.outbox.run",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="outbox",
        after=result,
    )
    db.commit()
    return result


# -------------------------------------------------------------- ops / DR

@router.get("/ops/dr-checklist")
def dr_backup_checklist(
    _: StaffContext = Depends(require_permissions("dashboard:read")),
    db: Session = Depends(get_db),
):
    """Phase B DR backup checklist stub — no full DR infra yet.

    RPO/RTO targets (CONTROL-SYSTEM-BUILD.md §11): document & test separately.
    Verify: Postgres/Neon PITR, encrypted secrets key escrow, outbox drain,
    restore drill of control.db / Neon snapshot, Nest m2m client rotation.
    """
    pending_outbox = db.execute(
        select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published.is_(False))
    ).scalar_one()
    return {
        "checklist": [
            {
                "id": "db_backup",
                "title": "Control DB backup / PITR enabled",
                "status": "manual_verify",
                "notes": "Neon PITR or pg_dump schedule; RPO ≤ 15m target",
            },
            {
                "id": "secrets_key",
                "title": "SECRETS_ENCRYPTION_KEY escrowed",
                "status": "manual_verify",
                "notes": "Store Fernet key in KMS/vault outside app host",
            },
            {
                "id": "outbox_drain",
                "title": "Outbox invalidation worker healthy",
                "status": "stub",
                "notes": f"Pending unpublished events: {pending_outbox}. Nest polls configVersion today.",
            },
            {
                "id": "restore_drill",
                "title": "Quarterly restore drill",
                "status": "manual_verify",
                "notes": "RTO ≤ 2h target; document runbook",
            },
            {
                "id": "m2m_rotation",
                "title": "m2m client secret rotation tested",
                "status": "manual_verify",
            },
        ],
        "outboxPending": pending_outbox,
        "configVersion": current_config_version(db),
    }


@router.get("/ops/outbox")
def list_outbox(
    unpublished_only: bool = False,
    _: StaffContext = Depends(require_permissions("dashboard:read")),
    db: Session = Depends(get_db),
):
    stmt = select(OutboxEvent).order_by(OutboxEvent.id.desc()).limit(50)
    if unpublished_only:
        stmt = stmt.where(OutboxEvent.published.is_(False))
    rows = db.execute(stmt).scalars().all()
    return [
        {
            "id": r.id,
            "eventType": r.event_type,
            "aggregateType": r.aggregate_type,
            "aggregateId": r.aggregate_id,
            "published": r.published,
            "publishedAt": r.published_at.isoformat() if r.published_at else None,
            "deliveryLog": json.loads(r.delivery_log or "[]") if r.delivery_log else [],
            "createdAt": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


# ----------------------------------------------------------- service clients

@router.get("/service-clients", response_model=list[ServiceClientOut])
def list_service_clients(
    _: StaffContext = Depends(require_permissions("service_clients:write")),
    db: Session = Depends(get_db),
):
    return db.execute(select(ServiceClient).order_by(ServiceClient.id)).scalars().all()


@router.post("/service-clients", response_model=ServiceClientCreated, status_code=201)
def create_service_client(
    body: ServiceClientCreate,
    staff: StaffContext = Depends(require_permissions("service_clients:write")),
    db: Session = Depends(get_db),
):
    plain_secret = generate_client_secret()
    client = ServiceClient(
        client_id=f"svc_{generate_client_secret()[:16]}",
        name=body.name,
        secret_hash=hash_client_secret(plain_secret),
        scopes=body.scopes,
        environment=body.environment,
    )
    db.add(client)
    record_audit(
        db,
        action="service_client.create",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="service_client",
        target_id=client.client_id,
        after={"name": body.name, "scopes": body.scopes, "environment": body.environment},
    )
    db.commit()
    db.refresh(client)
    base = ServiceClientOut.model_validate(client).model_dump()
    return ServiceClientCreated(**base, client_secret=plain_secret)


@router.delete("/service-clients/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_service_client(
    client_id: str,
    staff: StaffContext = Depends(require_permissions("service_clients:write")),
    db: Session = Depends(get_db),
):
    client = db.execute(
        select(ServiceClient).where(ServiceClient.client_id == client_id)
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service client not found")
    client.is_active = False
    record_audit(
        db,
        action="service_client.revoke",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="service_client",
        target_id=client_id,
    )
    db.commit()
