from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import bump_config_version, record_audit
from app.catalog import KNOWN_PROVIDERS
from app.database import get_db
from app.deps import StaffContext, client_ip, require_permissions
from app.models import ProviderConfig, ProviderFailoverPolicy, ProviderSecret, Tenant
from app.models.providers import CAPABILITIES
from app.schemas import (
    FailoverPolicyOut,
    FailoverPolicySet,
    ProviderConfigOut,
    ProviderConfigSet,
    ProviderSecretOut,
    ProviderSecretSet,
)
from app.security import encrypt_secret, secret_fingerprint
from app.services.dual_control import create_request, dual_control_enabled

router = APIRouter(prefix="/v1/providers", tags=["providers"])


@router.get("/capabilities")
def capabilities(_: StaffContext = Depends(require_permissions("providers:read"))):
    return {"capabilities": list(CAPABILITIES), "knownProviders": KNOWN_PROVIDERS}


@router.get("", response_model=list[ProviderConfigOut])
def list_configs(
    _: StaffContext = Depends(require_permissions("providers:read")),
    db: Session = Depends(get_db),
):
    return db.execute(
        select(ProviderConfig).order_by(ProviderConfig.capability, ProviderConfig.tenant_id)
    ).scalars().all()


@router.put("", response_model=ProviderConfigOut)
def set_config(
    body: ProviderConfigSet,
    request: Request,
    staff: StaffContext = Depends(require_permissions("providers:write")),
    db: Session = Depends(get_db),
):
    if body.capability not in CAPABILITIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown capability: {body.capability}")
    known = KNOWN_PROVIDERS.get(body.capability, [])
    if known and body.provider_id not in known:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown provider '{body.provider_id}' for {body.capability}. Known: {known}",
        )
    if body.mode not in ("sandbox", "live"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "mode must be sandbox or live")
    if body.tenant_id is not None and db.get(Tenant, body.tenant_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    if not body.reason:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reason required for provider changes")

    config = db.execute(
        select(ProviderConfig).where(
            ProviderConfig.capability == body.capability,
            ProviderConfig.tenant_id.is_(None)
            if body.tenant_id is None
            else ProviderConfig.tenant_id == body.tenant_id,
        )
    ).scalar_one_or_none()

    # Any change that crosses the live boundary needs dual-control — including
    # moving a live provider back to sandbox, or repointing an already-live
    # capability at a different provider. Previously only `mode == "live"` was
    # gated, so demoting live payments to sandbox needed no second approver.
    currently_live = config is not None and config.mode == "live"
    going_live = body.mode == "live"
    repointing_live = (
        currently_live and going_live and config is not None and config.provider_id != body.provider_id
    )
    needs_dual = going_live or currently_live or repointing_live

    if dual_control_enabled() and needs_dual:
        if config is None:
            # Do not persist a placeholder row before approval — an unapproved
            # request used to leave a half-created config behind.
            target_id = f"new:{body.capability}:{body.tenant_id or 'global'}"
        else:
            target_id = str(config.id)
        req = create_request(
            db,
            action="provider.switch_live" if going_live else "provider.switch_from_live",
            target_type="provider",
            target_id=target_id,
            payload={
                "provider_id": body.provider_id,
                "settings": body.settings,
                "capability": body.capability,
                "tenant_id": body.tenant_id,
                "mode": body.mode,
            },
            reason=body.reason,
            staff_id=staff.id,
            staff_email=staff.email,
        )
        db.commit()
        raise HTTPException(
            status.HTTP_202_ACCEPTED,
            f"Dual-control required for this live-provider change. Pending approval id={req.id}",
        )

    before = (
        {"provider_id": config.provider_id, "mode": config.mode, "settings": config.settings}
        if config
        else None
    )
    if config is None:
        config = ProviderConfig(capability=body.capability, tenant_id=body.tenant_id)
        db.add(config)
    config.provider_id = body.provider_id
    config.mode = body.mode
    config.settings = body.settings
    config.is_enabled = True

    record_audit(
        db,
        action="provider.switch",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="provider",
        target_id=f"{body.capability}:{body.tenant_id or 'global'}",
        reason=body.reason,
        before=before,
        after={"provider_id": body.provider_id, "mode": body.mode, "settings": body.settings},
        ip_address=client_ip(request),
    )
    bump_config_version(db)
    db.commit()
    db.refresh(config)
    return config


@router.get("/failover", response_model=list[FailoverPolicyOut])
def list_failover(
    _: StaffContext = Depends(require_permissions("providers:read")),
    db: Session = Depends(get_db),
):
    return db.execute(
        select(ProviderFailoverPolicy).order_by(ProviderFailoverPolicy.capability)
    ).scalars().all()


@router.put("/failover", response_model=FailoverPolicyOut)
def set_failover(
    body: FailoverPolicySet,
    staff: StaffContext = Depends(require_permissions("providers:write")),
    db: Session = Depends(get_db),
):
    if body.capability not in CAPABILITIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown capability: {body.capability}")
    if body.primary_provider_id == body.secondary_provider_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "primary and secondary must differ")
    row = db.execute(
        select(ProviderFailoverPolicy).where(
            ProviderFailoverPolicy.capability == body.capability,
            ProviderFailoverPolicy.tenant_id.is_(None)
            if body.tenant_id is None
            else ProviderFailoverPolicy.tenant_id == body.tenant_id,
        )
    ).scalar_one_or_none()
    if row is None:
        row = ProviderFailoverPolicy(capability=body.capability, tenant_id=body.tenant_id)
        db.add(row)
    row.primary_provider_id = body.primary_provider_id
    row.secondary_provider_id = body.secondary_provider_id
    row.enabled = body.enabled
    row.failure_threshold = body.failure_threshold
    row.cooldown_seconds = body.cooldown_seconds
    row.reason = body.reason
    record_audit(
        db,
        action="provider.failover.set",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="provider_failover",
        target_id=f"{body.capability}:{body.tenant_id or 'global'}",
        reason=body.reason,
        after={
            "primary": body.primary_provider_id,
            "secondary": body.secondary_provider_id,
            "enabled": body.enabled,
        },
    )
    bump_config_version(db)
    db.commit()
    db.refresh(row)
    return row


@router.post("/failover/{policy_id}/trip", response_model=FailoverPolicyOut)
def trip_failover(
    policy_id: int,
    staff: StaffContext = Depends(require_permissions("providers:write")),
    db: Session = Depends(get_db),
):
    """Ops signal: mark primary unhealthy → runtime uses secondary."""
    row = db.get(ProviderFailoverPolicy, policy_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Failover policy not found")
    row.consecutive_failures = (row.consecutive_failures or 0) + 1
    if row.consecutive_failures >= row.failure_threshold:
        row.is_tripped = True
        row.tripped_at = datetime.now(timezone.utc)
    record_audit(
        db,
        action="provider.failover.trip",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="provider_failover",
        target_id=str(policy_id),
        after={"is_tripped": row.is_tripped, "failures": row.consecutive_failures},
    )
    bump_config_version(db)
    db.commit()
    db.refresh(row)
    return row


@router.post("/failover/{policy_id}/reset", response_model=FailoverPolicyOut)
def reset_failover(
    policy_id: int,
    staff: StaffContext = Depends(require_permissions("providers:write")),
    db: Session = Depends(get_db),
):
    row = db.get(ProviderFailoverPolicy, policy_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Failover policy not found")
    row.is_tripped = False
    row.tripped_at = None
    row.consecutive_failures = 0
    record_audit(
        db,
        action="provider.failover.reset",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="provider_failover",
        target_id=str(policy_id),
    )
    bump_config_version(db)
    db.commit()
    db.refresh(row)
    return row


@router.get("/{config_id}/secrets", response_model=list[ProviderSecretOut])
def list_secrets(
    config_id: int,
    _: StaffContext = Depends(require_permissions("providers:read")),
    db: Session = Depends(get_db),
):
    """Metadata only — fingerprints, never plaintext."""
    return db.execute(
        select(ProviderSecret)
        .where(ProviderSecret.config_id == config_id)
        .order_by(ProviderSecret.key, ProviderSecret.version.desc())
    ).scalars().all()


@router.post("/{config_id}/test")
def test_connection(
    config_id: int,
    staff: StaffContext = Depends(require_permissions("providers:write")),
    db: Session = Depends(get_db),
):
    """Safe read-only vendor ping (balance/models/banks) — never charges or sends."""
    from app.services.provider_health import ping_provider

    config = db.get(ProviderConfig, config_id)
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider config not found")

    previous = config.health_status
    result = ping_provider(db, config)
    config.health_status = result["healthStatus"]
    record_audit(
        db,
        action="provider.test",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="provider",
        target_id=f"{config.capability}:{config.provider_id}",
        before={"health_status": previous},
        after={
            "health_status": config.health_status,
            "ok": result["ok"],
            "message": result["message"],
        },
        reason="Provider connection test",
    )
    db.commit()
    return {
        "ok": result["ok"],
        "capability": config.capability,
        "providerId": config.provider_id,
        "mode": config.mode,
        "healthStatus": config.health_status,
        "message": result["message"],
        "detail": result.get("detail") or {},
    }


@router.put("/{config_id}/secrets", response_model=ProviderSecretOut)
def set_secret(
    config_id: int,
    body: ProviderSecretSet,
    staff: StaffContext = Depends(require_permissions("secrets:write")),
    db: Session = Depends(get_db),
):
    config = db.get(ProviderConfig, config_id)
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider config not found")

    # Retire previous versions of the same key
    previous = db.execute(
        select(ProviderSecret).where(
            ProviderSecret.config_id == config_id,
            ProviderSecret.key == body.key,
            ProviderSecret.is_active.is_(True),
        )
    ).scalars().all()
    next_version = max((s.version for s in previous), default=0) + 1
    for secret in previous:
        secret.is_active = False

    record = ProviderSecret(
        config_id=config_id,
        key=body.key,
        ciphertext=encrypt_secret(body.value),
        fingerprint=secret_fingerprint(body.value),
        version=next_version,
        created_by=staff.email,
    )
    db.add(record)

    record_audit(
        db,
        action="provider.secret.rotate",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="provider",
        target_id=f"{config.capability}:{config.provider_id}",
        after={"key": body.key, "version": next_version, "fingerprint": record.fingerprint},
    )
    bump_config_version(db)
    db.commit()
    db.refresh(record)
    return record
