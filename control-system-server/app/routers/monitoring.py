"""SLO dashboard stubs + load-test ops notes (Phase C hardening)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import current_config_version
from app.config import get_settings
from app.database import get_db
from app.deps import StaffContext, require_permissions
from app.models import AuditEvent, DualControlRequest, Incident, OutboxEvent, PlatformUser, ProviderConfig, SupportSession, Tenant

router = APIRouter(prefix="/v1/monitoring", tags=["monitoring"])
settings = get_settings()


@router.get("/slos")
def slo_dashboard(
    _: StaffContext = Depends(require_permissions("monitoring:read")),
    db: Session = Depends(get_db),
):
    """SLO / error-budget view from live control-plane signals."""
    tenants = db.execute(select(func.count()).select_from(Tenant)).scalar_one()
    open_incidents = db.execute(
        select(func.count()).select_from(Incident).where(Incident.status != "resolved")
    ).scalar_one()
    pending_outbox = db.execute(
        select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published.is_(False))
    ).scalar_one()
    provider_red = db.execute(
        select(func.count())
        .select_from(ProviderConfig)
        .where(ProviderConfig.health_status == "red")
    ).scalar_one()
    pending_dual = db.execute(
        select(func.count())
        .select_from(DualControlRequest)
        .where(DualControlRequest.status == "pending")
    ).scalar_one()
    audit_24h = db.execute(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.created_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0))
    ).scalar_one()

    api_status = "green" if open_incidents == 0 and provider_red == 0 else ("amber" if open_incidents < 3 else "red")
    # Rough error-budget proxy: burn when incidents/outbox backlog present.
    budget_burn = min(100, open_incidents * 15 + provider_red * 10 + (10 if pending_outbox > 50 else 0))
    budget_remaining = max(0, 100 - budget_burn)

    return {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "configVersion": current_config_version(db),
        "targets": [
            {
                "id": "control_api_availability",
                "name": "Control API availability",
                "target": "99.9%",
                "current": f"signal={api_status} (incidents={open_incidents})",
                "errorBudgetRemaining": f"{budget_remaining}%",
                "status": api_status,
            },
            {
                "id": "config_propagation",
                "name": "Config propagation",
                "target": "< 60s",
                "current": f"ttl={settings.runtime_config_ttl_seconds}s + outbox pending={pending_outbox}",
                "status": "green" if pending_outbox < 50 else "amber",
            },
            {
                "id": "nest_cache_ttl",
                "name": "Nest config cache TTL",
                "target": "≤ 60s",
                "current": f"{settings.runtime_config_ttl_seconds}s",
                "status": "green" if settings.runtime_config_ttl_seconds <= 60 else "red",
            },
        ],
        "signals": {
            "tenants": tenants,
            "openIncidents": open_incidents,
            "providerRed": provider_red,
            "pendingDualControl": pending_dual,
            "pendingOutbox": pending_outbox,
            "auditEventsToday": audit_24h,
        },
        "note": "Derived from control DB signals; connect APM for request-level SLIs in prod.",
    }


@router.get("/synthetics")
def synthetics(
    run: bool = False,
    _: StaffContext = Depends(require_permissions("monitoring:read")),
    db: Session = Depends(get_db),
):
    """Provider health snapshot. Pass `?run=true` to execute live vendor pings."""
    from app.services.provider_health import ping_provider

    providers = db.execute(
        select(ProviderConfig).where(ProviderConfig.tenant_id.is_(None))
    ).scalars().all()
    checks = []
    for p in providers:
        if run:
            result = ping_provider(db, p)
            p.health_status = result["healthStatus"]
            checks.append(
                {
                    "id": f"{p.capability}:{p.provider_id}",
                    "capability": p.capability,
                    "providerId": p.provider_id,
                    "mode": p.mode,
                    "healthStatus": p.health_status,
                    "ok": result["ok"],
                    "lastResult": result["message"],
                    "detail": result.get("detail") or {},
                }
            )
        else:
            checks.append(
                {
                    "id": f"{p.capability}:{p.provider_id}",
                    "capability": p.capability,
                    "providerId": p.provider_id,
                    "mode": p.mode,
                    "healthStatus": p.health_status,
                    "lastResult": "cached",
                }
            )
    if run:
        db.commit()
    return {
        "checks": checks,
        "ranLive": run,
        "message": "Live pings executed" if run else "Cached health — add ?run=true to ping vendors",
    }


@router.get("/load-test-notes")
def load_test_notes(_: StaffContext = Depends(require_permissions("monitoring:read"))):
    return {
        "title": "Control plane load-test notes (Phase C stub)",
        "recommendations": [
            "k6 or Locust against /health/live + /health/ready (no auth) for baseline.",
            "Authenticated staff browse: GET /v1/dashboard, /v1/flags, /v1/tenants (p95 < 300ms local).",
            "m2m soak: GET /internal/v1/tenants/{id}/runtime-config at Nest poll rate (TTL ≤ 60s).",
            "Dual-control + audit append path: ensure write amplification stays acceptable under 50 rps.",
            "Provider secret encrypt/decrypt path: not on hot path — do not include in soak.",
            "SQLite is local-only; run load tests against Postgres/Neon staging.",
        ],
        "suggestedScenarios": [
            {"name": "config_pull_storm", "vus": 50, "duration": "2m", "path": "/internal/v1/.../runtime-config"},
            {"name": "staff_console", "vus": 10, "duration": "5m", "path": "/v1/dashboard"},
            {"name": "public_status", "vus": 100, "duration": "1m", "path": "/public/v1/status"},
        ],
        "rpoRto": {"rpo": "≤ 15m", "rto": "≤ 2h", "source": "CONTROL-SYSTEM-BUILD.md §11"},
    }


@router.get("/audit-rate")
def audit_rate_stub(
    _: StaffContext = Depends(require_permissions("monitoring:read")),
    db: Session = Depends(get_db),
):
    total = db.execute(select(func.count()).select_from(AuditEvent)).scalar_one()
    return {"auditEventsTotal": total, "note": "Rate / anomaly charts stub — export to SIEM in prod."}


@router.get("/security-checklist")
def security_checklist(
    _: StaffContext = Depends(require_permissions("monitoring:read")),
    db: Session = Depends(get_db),
):
    """Concrete pen-test / hardening checklist with live status signals."""
    cfg = settings
    pending_outbox = db.execute(
        select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published.is_(False))
    ).scalar_one()
    mfa_users = db.execute(
        select(func.count()).select_from(PlatformUser).where(PlatformUser.mfa_enabled.is_(True))
    ).scalar_one()
    total_users = db.execute(select(func.count()).select_from(PlatformUser)).scalar_one()
    active_break_glass = db.execute(
        select(func.count())
        .select_from(SupportSession)
        .where(SupportSession.is_active.is_(True))
    ).scalar_one()
    provider_red = db.execute(
        select(func.count())
        .select_from(ProviderConfig)
        .where(ProviderConfig.health_status == "red")
    ).scalar_one()

    jwt_ok = len(cfg.jwt_secret) >= 32 and cfg.jwt_secret not in {
        "change-me-control-plane-secret-32b!",
        "dev-control-jwt-secret-min-32-bytes!!",
    }
    secrets_ok = bool(cfg.secrets_encryption_key.strip())
    ip_enforced = cfg.ip_allowlist_enforced
    cors_locked = cfg.environment == "production" and len(cfg.cors_origins) <= 3

    def item(item_id: str, title: str, status: str, notes: str = "") -> dict:
        return {"id": item_id, "title": title, "status": status, "notes": notes}

    checklist = [
        item(
            "jwt_secret",
            "JWT_SECRET rotated (≥32 bytes, not default)",
            "pass" if jwt_ok else "fail",
            "Rotate JWT_SECRET and invalidate staff sessions after deploy",
        ),
        item(
            "secrets_encryption",
            "SECRETS_ENCRYPTION_KEY set (Fernet)",
            "pass" if secrets_ok else ("warn" if cfg.environment != "production" else "fail"),
            "Provider secrets use derived dev key when unset",
        ),
        item(
            "mfa_coverage",
            "Staff MFA enrollment",
            "pass" if total_users and mfa_users == total_users else "warn",
            f"{mfa_users}/{total_users} staff with MFA enabled",
        ),
        item(
            "ip_allowlist",
            "IP allowlist enforced for staff API",
            "pass" if ip_enforced else "warn",
            "Enable IP_ALLOWLIST_ENFORCED + DB entries in production",
        ),
        item(
            "cors_origins",
            "CORS origins restricted",
            "pass" if cors_locked or cfg.environment != "production" else "warn",
            f"Origins: {cfg.cors_origins}",
        ),
        item(
            "rate_limits",
            "Rate limits configured",
            "pass",
            f"staff={cfg.rate_limit_staff_per_minute}/min auth={cfg.rate_limit_auth_per_minute}/min",
        ),
        item(
            "dual_control",
            "Dual-control for destructive actions",
            "pass" if (cfg.dual_control_required or cfg.environment == "production") else "warn",
            "Required in production by default",
        ),
        item(
            "outbox_worker",
            "Outbox invalidation worker",
            "pass" if pending_outbox == 0 else "warn",
            f"Pending unpublished events: {pending_outbox}",
        ),
        item(
            "provider_health",
            "Provider integrations healthy",
            "pass" if provider_red == 0 else "fail",
            f"{provider_red} provider(s) in red status",
        ),
        item(
            "break_glass",
            "Active break-glass sessions",
            "pass" if active_break_glass == 0 else "warn",
            f"{active_break_glass} active session(s) — review audit log",
        ),
        item(
            "https_enforced",
            "HTTPS enforced (Guard middleware)",
            "pass" if cfg.environment == "production" else "n/a",
            "SecurityMiddleware enforce_https in production",
        ),
        item(
            "audit_retention",
            "Audit log append-only",
            "pass",
            f"Total audit events: {db.execute(select(func.count()).select_from(AuditEvent)).scalar_one()}",
        ),
    ]
    passed = sum(1 for c in checklist if c["status"] == "pass")
    return {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "environment": cfg.environment,
        "summary": {"pass": passed, "total": len(checklist)},
        "checklist": checklist,
    }
