"""m2m routes for school Nest servers (`/internal/v1/...`).

Staff browser tokens are rejected here; only service-client m2m tokens with
the right scopes pass (CONTROL-SYSTEM-BUILD.md §5.17 / §6).
"""

import hashlib

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.audit import record_audit
from app.database import get_db
from app.deps import ServiceContext, require_scope
from app.models import (
    FeatureFlag,
    Plan,
    ProviderConfig,
    ProviderSecret,
    SaasInvoice,
    SaasPayment,
    SchoolBlacklistEntry,
    ServiceClient,
    Subscription,
    Tenant,
    TenantOffboardJob,
    TenantProvisioningJob,
    TenantInstall,
)
from app.schemas import (
    CatalogSubscriptionChange,
    M2MTokenRequest,
    PlanOut,
    RegistrationCheckRequest,
    SubscriptionOut,
    ProvisioningResult,
)
from app.security import (
    client_secret_needs_rehash,
    create_m2m_token,
    decrypt_secret,
    hash_client_secret,
    verify_client_secret,
)
from app.services.runtime_config import build_runtime_config
from app.services.billing import (
    apply_payment_to_invoice,
    create_invoice_for_subscription,
)
from app.services.offboarding import complete_offboard_job
from app.config import get_settings
from app.services.invoice_pdf import build_invoice_pdf
from app.services.provisioning import apply_initialization_result
from app.services.trials import ensure_install, initialize_trial_once, install_matches

router = APIRouter(prefix="/internal/v1", tags=["internal-m2m"])


@router.post("/token")
def issue_token(body: M2MTokenRequest, db: Session = Depends(get_db)):
    client = db.execute(
        select(ServiceClient).where(ServiceClient.client_id == body.client_id)
    ).scalar_one_or_none()
    authenticated = (
        client is not None
        and client.is_active
        and verify_client_secret(body.client_secret, client.secret_hash)
    )
    if not authenticated:
        record_audit(
            db,
            action="m2m.token.denied",
            actor_type="service",
            actor_id=body.client_id[:64],
            target_type="service_client",
            target_id=body.client_id[:64],
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid client credentials")
    if client_secret_needs_rehash(client.secret_hash):
        client.secret_hash = hash_client_secret(body.client_secret)
        db.commit()
    return {
        "access_token": create_m2m_token(client.client_id, client.scopes),
        "token_type": "bearer",
        "scopes": client.scopes,
    }


def _tenant_by_ref(
    db: Session, tenant_ref: str, svc: ServiceContext | None = None
) -> Tenant:
    """Look up by external id first, then slug, then enforce the client's tenant
    allowlist when one is configured."""
    tenant = db.execute(
        select(Tenant).where(Tenant.external_id == tenant_ref)
    ).scalar_one_or_none()
    if tenant is None:
        tenant = db.execute(
            select(Tenant).where(Tenant.slug == tenant_ref)
        ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Tenant not registered in control plane"
        )
    if svc is not None:
        allowed = list(svc.client.allowed_tenant_refs or [])
        if allowed and not {tenant.external_id, tenant.slug} & set(allowed):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Service client is not scoped to this tenant",
            )
    return tenant


@router.get("/plans", response_model=list[PlanOut])
def catalog_plans(
    _: ServiceContext = Depends(require_scope("plans:read")),
    db: Session = Depends(get_db),
):
    """Active catalog plans for school-facing subscription screens."""
    plans = db.execute(
        select(Plan)
        .options(selectinload(Plan.entitlements))
        .where(Plan.is_active.is_(True))
        .order_by(Plan.rank)
    ).scalars().all()
    feature_names = {
        flag.key: flag.name
        for flag in db.execute(select(FeatureFlag)).scalars().all()
    }
    return [
        {
            "id": plan.id,
            "key": plan.key,
            "name": plan.name,
            "description": plan.description,
            "monthly_price_minor": plan.monthly_price_minor,
            "currency": plan.currency,
            "rank": plan.rank,
            "is_active": plan.is_active,
            "entitlements": [
                {
                    "feature_key": entitlement.feature_key,
                    "name": feature_names.get(entitlement.feature_key, entitlement.feature_key),
                    "enabled": entitlement.enabled,
                    "quota": entitlement.quota,
                }
                for entitlement in plan.entitlements
            ],
        }
        for plan in plans
    ]


@router.get("/tenants/{tenant_ref}/subscription", response_model=SubscriptionOut)
def tenant_subscription(
    tenant_ref: str,
    svc: ServiceContext = Depends(require_scope("subscriptions:read")),
    db: Session = Depends(get_db),
):
    tenant = _tenant_by_ref(db, tenant_ref, svc)
    subscription = db.execute(
        select(Subscription)
        .where(Subscription.tenant_id == tenant.id, Subscription.is_current.is_(True))
        .order_by(Subscription.id.desc())
    ).scalars().first()
    if subscription is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No current subscription")
    return subscription


@router.post("/tenants/{tenant_ref}/subscription", response_model=SubscriptionOut)
def change_tenant_subscription(
    tenant_ref: str,
    body: CatalogSubscriptionChange,
    svc: ServiceContext = Depends(require_scope("subscriptions:write")),
    db: Session = Depends(get_db),
):
    tenant = _tenant_by_ref(db, tenant_ref, svc)
    if body.billing_cycle not in ("monthly", "yearly"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid billing cycle")
    plan = db.execute(
        select(Plan).where(Plan.key == body.plan_key, Plan.is_active.is_(True))
    ).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown or inactive plan")

    for existing in db.execute(
        select(Subscription).where(
            Subscription.tenant_id == tenant.id,
            Subscription.is_current.is_(True),
        )
    ).scalars():
        existing.is_current = False

    period_price = int(plan.monthly_price_minor or 0)
    if body.billing_cycle == "yearly":
        period_price *= 12
    subscription = Subscription(
        tenant_id=tenant.id,
        type="catalog",
        plan_id=plan.id,
        # Plan changes are payment-gated just like custom deals.
        status="past_due",
        billing_cycle=body.billing_cycle,
        price_minor=period_price,
        currency=plan.currency,
        is_current=True,
    )
    db.add(subscription)
    db.flush()
    invoice = create_invoice_for_subscription(
        db, subscription=subscription, staff_email=svc.client.client_id
    )
    record_audit(
        db,
        action="subscription.catalog.change",
        actor_type="service",
        actor_id=svc.client.client_id,
        target_type="tenant",
        target_id=tenant.external_id,
        after={"plan_key": plan.key, "billing_cycle": body.billing_cycle},
    )
    db.commit()
    db.refresh(subscription)
    result = SubscriptionOut.model_validate(subscription).model_dump()
    result["pending_invoice_id"] = invoice.id if invoice else None
    return result


@router.get("/tenants/{tenant_ref}/invoices")
def tenant_invoices(
    tenant_ref: str,
    svc: ServiceContext = Depends(require_scope("billing:read")),
    db: Session = Depends(get_db),
):
    tenant = _tenant_by_ref(db, tenant_ref, svc)
    invoices = db.execute(
        select(SaasInvoice)
        .where(SaasInvoice.tenant_id == tenant.id)
        .order_by(SaasInvoice.id.desc())
        .limit(200)
    ).scalars().all()
    return [
        {
            "id": invoice.id,
            "invoiceNo": invoice.number,
            "period": invoice.period_key,
            "amount": invoice.amount_minor / 100,
            "amountMinor": invoice.amount_minor,
            "amountPaidMinor": invoice.amount_paid_minor,
            "currency": invoice.currency,
            "status": invoice.status,
            "dueDate": invoice.due_at,
            "paidAt": invoice.paid_at,
            "createdAt": invoice.created_at,
            "subscriptionId": invoice.subscription_id,
        }
        for invoice in invoices
    ]


@router.get("/tenants/{tenant_ref}/invoices/{invoice_id}/pdf")
def tenant_invoice_pdf(
    tenant_ref: str,
    invoice_id: int,
    svc: ServiceContext = Depends(require_scope("billing:read")),
    db: Session = Depends(get_db),
):
    tenant = _tenant_by_ref(db, tenant_ref, svc)
    invoice = db.get(SaasInvoice, invoice_id)
    if invoice is None or invoice.tenant_id != tenant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    pdf = build_invoice_pdf(
        issuer=get_settings().invoice_issuer_name,
        invoice_number=invoice.number,
        tenant_name=tenant.name,
        tenant_slug=tenant.slug,
        amount_minor=invoice.amount_minor,
        currency=invoice.currency,
        tax_minor=getattr(invoice, "tax_minor", 0) or 0,
        status=invoice.status,
        notes=invoice.notes,
        period_start=invoice.period_start,
        period_end=invoice.period_end,
        due_at=invoice.due_at,
        paid_at=invoice.paid_at,
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{invoice.number}.pdf"'},
    )


@router.post("/tenants/{tenant_ref}/invoices/{invoice_id}/settle")
def settle_tenant_invoice(
    tenant_ref: str,
    invoice_id: int,
    body: dict,
    svc: ServiceContext = Depends(require_scope("billing:write")),
    db: Session = Depends(get_db),
):
    tenant = _tenant_by_ref(db, tenant_ref, svc)
    invoice = db.get(SaasInvoice, invoice_id)
    if invoice is None or invoice.tenant_id != tenant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    amount_minor = int(body.get("amountMinor") or 0)
    provider_reference = str(body.get("providerReference") or "")
    if not provider_reference:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Payment reference is required")
    duplicate = db.execute(
        select(SaasPayment).where(SaasPayment.provider_reference == provider_reference)
    ).scalars().first()
    if duplicate is not None:
        if duplicate.tenant_id != tenant.id or duplicate.invoice_id != invoice.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Payment reference already used")
        return {
            "invoiceId": invoice.id,
            "status": invoice.status,
            "settled": invoice.status == "paid",
            "duplicate": True,
        }
    outstanding = max(0, int(invoice.amount_minor or 0) - int(invoice.amount_paid_minor or 0))
    if amount_minor <= 0 or amount_minor > outstanding:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid invoice payment amount")
    payment = SaasPayment(
        invoice_id=invoice.id,
        tenant_id=tenant.id,
        amount_minor=amount_minor,
        currency=invoice.currency,
        method="gateway",
        provider_id=str(body.get("providerId") or "unknown"),
        provider_reference=provider_reference,
        recorded_by=f"m2m:{svc.client.client_id}",
        notes="School SaaS subscription checkout",
    )
    db.add(payment)
    result = apply_payment_to_invoice(
        db,
        invoice,
        amount_minor,
        staff_email=f"m2m:{svc.client.client_id}",
    )
    db.commit()
    return result


@router.get("/tenants/{tenant_ref}/runtime-config")
def runtime_config(
    tenant_ref: str,
    svc: ServiceContext = Depends(require_scope("runtime-config:read")),
    db: Session = Depends(get_db),
):
    tenant = _tenant_by_ref(db, tenant_ref, svc)
    return build_runtime_config(db, tenant)


@router.post("/tenants/{tenant_ref}/offboard/{job_id}/result")
def offboard_result(
    tenant_ref: str,
    job_id: int,
    body: dict,
    svc: ServiceContext = Depends(require_scope("offboarding:write")),
    db: Session = Depends(get_db),
):
    """Record export/wipe completion from the school server."""
    tenant = _tenant_by_ref(db, tenant_ref, svc)
    job = db.get(TenantOffboardJob, job_id)
    if job is None or job.tenant_id != tenant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Offboard job not found")
    result = str(body.get("result") or "")
    if result not in ("export_ready", "wipe_completed", "failed"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown offboard result")
    if result == "export_ready":
        job.status = "wipe_scheduled"
        job.export_package_uri = str(body.get("exportUri") or job.export_package_uri)
        detail = "Export package is ready"
    else:
        complete_offboard_job(
            db,
            job,
            ok=result == "wipe_completed",
            detail=str(body.get("detail") or ""),
        )
        detail = "Offboard job updated"
    record_audit(
        db,
        action="tenant.offboard.result",
        actor_type="service",
        actor_id=svc.client.client_id,
        target_type="tenant_offboard_job",
        target_id=str(job.id),
        after={"result": result, "export_uri": job.export_package_uri},
    )
    db.commit()
    return {"accepted": True, "jobId": job.id, "status": job.status, "message": detail}


@router.post("/tenants/{tenant_ref}/provision/{job_id}/result")
def provisioning_result(
    tenant_ref: str,
    job_id: int,
    body: ProvisioningResult,
    svc: ServiceContext = Depends(require_scope("runtime-config:read")),
    db: Session = Depends(get_db),
):
    """Accept the school server's observed initialization result.

    A successful result must explicitly report ``school_db_status=ready``;
    merely delivering the initialization event never changes the tenant to
    active.
    """
    tenant = _tenant_by_ref(db, tenant_ref, svc)
    job = db.get(TenantProvisioningJob, job_id)
    if job is None or job.tenant_id != tenant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provisioning job not found")
    if body.status not in ("running", "succeeded", "failed"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown provisioning status")
    if body.school_db_status not in ("pending", "initializing", "ready", "failed"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown school DB status")
    if body.status == "succeeded" and body.school_db_status != "ready":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "succeeded requires school_db_status=ready",
        )
    before = {
        "status": job.status,
        "schoolDbStatus": job.school_db_status,
    }
    apply_initialization_result(
        db,
        tenant=tenant,
        job=job,
        status=body.status,
        school_db_status=body.school_db_status,
        error=body.error,
        result=body.result,
    )
    record_audit(
        db,
        action="tenant.provision.result",
        actor_type="service",
        actor_id=svc.client.client_id,
        target_type="tenant_provisioning_job",
        target_id=str(job.id),
        before=before,
        after={
            "status": job.status,
            "schoolDbStatus": job.school_db_status,
            "schoolDbProvisioned": job.school_db_status == "ready",
        },
    )
    db.commit()
    return {
        "accepted": True,
        "jobId": job.id,
        "status": job.status,
        "schoolDbStatus": job.school_db_status,
        "schoolDbProvisioned": job.school_db_status == "ready",
    }


@router.get("/tenants/{tenant_ref}/provider-secrets/{capability}")
def provider_secrets(
    tenant_ref: str,
    capability: str,
    svc: ServiceContext = Depends(require_scope("provider-secrets:read")),
    db: Session = Depends(get_db),
):
    """Sealed secret material for the tenant's active provider. Fetched
    separately from runtime config so secrets never ride the cached payload."""
    tenant = _tenant_by_ref(db, tenant_ref, svc)

    config = db.execute(
        select(ProviderConfig).where(
            ProviderConfig.capability == capability,
            ProviderConfig.tenant_id == tenant.id,
            ProviderConfig.is_enabled.is_(True),
        )
    ).scalar_one_or_none()
    if config is None:
        config = db.execute(
            select(ProviderConfig).where(
                ProviderConfig.capability == capability,
                ProviderConfig.tenant_id.is_(None),
                ProviderConfig.is_enabled.is_(True),
            )
        ).scalar_one_or_none()
    if config is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No active provider for {capability}"
        )

    secrets = (
        db.execute(
            select(ProviderSecret).where(
                ProviderSecret.config_id == config.id,
                ProviderSecret.is_active.is_(True),
            )
        )
        .scalars()
        .all()
    )

    record_audit(
        db,
        action="provider.secret.fetch",
        actor_type="service",
        actor_id=svc.client.client_id,
        target_type="provider",
        target_id=f"{capability}:{config.provider_id}",
        after={
            "tenantRef": tenant.external_id or tenant.slug,
            "tenantId": tenant.id,
            "scope": "tenant" if config.tenant_id else "global",
            "secretKeys": sorted(s.key for s in secrets),
        },
    )
    db.commit()

    return {
        "capability": capability,
        "providerId": config.provider_id,
        "mode": config.mode,
        "settings": config.settings,
        "secrets": {s.key: decrypt_secret(s.ciphertext) for s in secrets},
        "secretVersions": {s.key: s.version for s in secrets},
    }


@router.get("/provider-secrets/{capability}")
def global_provider_secrets(
    capability: str,
    svc: ServiceContext = Depends(require_scope("provider-secrets:read")),
    db: Session = Depends(get_db),
):
    """Platform-level provider secrets used before a tenant exists."""
    config = db.execute(
        select(ProviderConfig).where(
            ProviderConfig.capability == capability,
            ProviderConfig.tenant_id.is_(None),
            ProviderConfig.is_enabled.is_(True),
        )
    ).scalar_one_or_none()
    if config is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No active provider for {capability}"
        )
    secrets = (
        db.execute(
            select(ProviderSecret).where(
                ProviderSecret.config_id == config.id,
                ProviderSecret.is_active.is_(True),
            )
        )
        .scalars()
        .all()
    )
    record_audit(
        db,
        action="provider.secret.fetch",
        actor_type="service",
        actor_id=svc.client.client_id,
        target_type="provider",
        target_id=f"{capability}:{config.provider_id}",
        after={"scope": "global", "secretKeys": sorted(s.key for s in secrets)},
    )
    db.commit()
    return {
        "capability": capability,
        "providerId": config.provider_id,
        "mode": config.mode,
        "settings": config.settings,
        "secrets": {s.key: decrypt_secret(s.ciphertext) for s in secrets},
        "secretVersions": {s.key: s.version for s in secrets},
    }


@router.post("/registration-check")
def registration_check(
    body: RegistrationCheckRequest,
    _: ServiceContext = Depends(require_scope("runtime-config:read")),
    db: Session = Depends(get_db),
):
    """School signup gate: reject when identity fingerprints match the blacklist."""
    entries = (
        db.execute(
            select(SchoolBlacklistEntry).where(SchoolBlacklistEntry.is_active.is_(True))
        )
        .scalars()
        .all()
    )

    name = body.school_name.strip().lower()
    email = body.admin_email.strip().lower()
    email_domain = email.split("@")[-1] if "@" in email else ""
    slug = body.slug.strip().lower()
    phone = body.phone.strip()

    for entry in entries:
        matches = []
        if (
            name
            and entry.school_name_normalized
            and name == entry.school_name_normalized
        ):
            matches.append("school_name")
        if slug and entry.slug and slug == entry.slug:
            matches.append("slug")
        if email and entry.admin_email and email == entry.admin_email:
            matches.append("admin_email")
        if email_domain and entry.email_domain and email_domain == entry.email_domain:
            matches.append("email_domain")
        if phone and entry.phone and phone == entry.phone:
            matches.append("phone")
        # Email domain alone is a weak signal (gmail.com etc.) — require a
        # stronger fingerprint or a second signal.
        strong = [m for m in matches if m != "email_domain"]
        if strong or len(matches) >= 2:
            return {
                "allowed": False,
                "matches": matches,
                "message": "Registration cannot be completed. Please contact support.",
            }

    if body.install_token or body.device_hash:
        token_hash = (
            hashlib.sha256(body.install_token.strip().encode()).hexdigest()
            if body.install_token.strip()
            else ""
        )
        device_hash = (
            hashlib.sha256(body.device_hash.strip().encode()).hexdigest()
            if body.device_hash.strip()
            else ""
        )
        install_match = db.execute(
            select(TenantInstall).where(
                TenantInstall.revoked_at.is_(None),
                (
                    (TenantInstall.install_token_hash == token_hash)
                    if token_hash
                    else TenantInstall.device_hash == device_hash
                ),
            )
        ).scalars().first()
        if install_match is not None:
            return {
                "allowed": False,
                "matches": ["install_token" if token_hash else "device_hash"],
                "message": "This device has already been used to register a school. Please contact support.",
            }

    if body.install_token and body.device_hash and body.slug:
        tenant = db.execute(select(Tenant).where(Tenant.slug == slug)).scalar_one_or_none()
        if tenant is not None:
            install = db.execute(
                select(TenantInstall).where(TenantInstall.tenant_id == tenant.id)
            ).scalar_one_or_none()
            if install is not None and not install_matches(
                install, body.install_token, body.device_hash
            ):
                return {
                    "allowed": False,
                    "matches": ["install_identity"],
                    "message": "This installation is not recognized.",
                }
    return {"allowed": True, "matches": [], "message": None}


@router.post("/tenants/{tenant_ref}/register")
def register_tenant(
    tenant_ref: str,
    body: dict,
    svc: ServiceContext = Depends(require_scope("runtime-config:read")),
    db: Session = Depends(get_db),
):
    """Idempotent: the school server calls this after a successful signup so
    the control plane knows the tenant (default plan: basic)."""
    slug = str(body.get("slug") or tenant_ref).strip().lower()
    device_hash = str(body.get("deviceHash") or body.get("device_hash") or "")
    supplied_token = str(body.get("installToken") or body.get("install_token") or "")
    email = str(body.get("adminEmail") or "").strip().lower()
    school_name = str(body.get("name") or tenant_ref).strip().lower()
    email_domain = email.split("@")[-1] if "@" in email else ""
    blacklist_entries = db.execute(
        select(SchoolBlacklistEntry).where(SchoolBlacklistEntry.is_active.is_(True))
    ).scalars().all()
    for entry in blacklist_entries:
        matches = (
            (entry.slug and entry.slug == slug),
            (entry.admin_email and entry.admin_email == email),
            (entry.school_name_normalized and entry.school_name_normalized == school_name),
            (entry.email_domain and entry.email_domain == email_domain),
        )
        if any(matches[:3]) or sum(bool(match) for match in matches) >= 2:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Registration cannot be completed. Please contact support.",
            )

    existing = db.execute(
        select(Tenant).where(Tenant.external_id == tenant_ref)
    ).scalar_one_or_none()
    if existing is not None:
        install, _ = ensure_install(db, tenant=existing, device_hash=device_hash)
        if supplied_token and not install_matches(install, supplied_token, device_hash):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Installation credentials are invalid")
        db.commit()
        return {"created": False, "tenantId": existing.external_id, "installToken": None}

    tenant = Tenant(
        external_id=tenant_ref,
        slug=slug,
        name=str(body.get("name") or tenant_ref),
        admin_email=email,
        admin_phone=str(body.get("adminPhone") or ""),
        region=str(body.get("region") or "NG"),
        status="pending_verification",
    )
    db.add(tenant)
    db.flush()

    plan = db.execute(select(Plan).where(Plan.key == "basic")).scalar_one_or_none()
    if plan is not None:
        db.add(
            Subscription(
                tenant_id=tenant.id,
                type="catalog",
                plan_id=plan.id,
                status="trialing",
                notes="Automatic one-time 90-day onboarding trial",
            )
        )
    _, install_token = ensure_install(
        db, tenant=tenant, token=supplied_token or None, device_hash=device_hash
    )

    record_audit(
        db,
        action="tenant.self_register",
        actor_type="service",
        actor_id=svc.client.client_id,
        target_type="tenant",
        target_id=tenant.slug,
        after={"name": tenant.name},
    )
    db.commit()
    return {"created": True, "tenantId": tenant.external_id, "installToken": install_token}


@router.post("/tenants/{tenant_ref}/activate")
def activate_tenant(
    tenant_ref: str,
    svc: ServiceContext = Depends(require_scope("runtime-config:read")),
    db: Session = Depends(get_db),
):
    """Activate a school only after its school-server email verification succeeds."""
    tenant = _tenant_by_ref(db, tenant_ref, svc)
    if tenant.status == "blacklisted":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Tenant is blacklisted")
    if tenant.status not in ("pending_verification", "provisioning", "active"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Tenant cannot be activated from its current state",
        )
    if tenant.status != "active":
        tenant.status = "active"
        tenant.status_reason = ""
        tenant.status_message = ""
        record_audit(
            db,
            action="tenant.email_verified",
            actor_type="service",
            actor_id=svc.client.client_id,
            target_type="tenant",
            target_id=tenant.external_id,
        )
    initialize_trial_once(db, tenant=tenant)
    db.commit()
    return {"activated": True, "tenantId": tenant.external_id}


@router.post("/usage")
def ingest_usage(
    body: dict,
    svc: ServiceContext = Depends(require_scope("usage:write")),
    db: Session = Depends(get_db),
):
    """Usage event ingestion — persist meters/rollups (Phase B)."""
    from app.services.usage_metering import ensure_default_meters, ingest_usage_event

    ensure_default_meters(db)
    event = ingest_usage_event(
        db,
        tenant_ref=str(body.get("tenant_ref") or body.get("tenantId") or "") or None,
        meter_key=str(body.get("meter_key") or body.get("meterKey") or "unknown"),
        quantity=int(body.get("quantity") or 1),
        event_id=str(body.get("event_id") or body.get("eventId") or ""),
        properties=(
            body.get("properties") if isinstance(body.get("properties"), dict) else body
        ),
    )
    record_audit(
        db,
        action="usage.ingest",
        actor_type="service",
        actor_id=svc.client.client_id,
        target_type="usage",
        target_id=str(event.id),
        after=body if isinstance(body, dict) else None,
    )
    db.commit()
    return {"accepted": True, "eventId": event.id}


@router.post("/abuse/signals")
def ingest_abuse_signal(
    body: dict,
    svc: ServiceContext = Depends(require_scope("abuse:write")),
    db: Session = Depends(get_db),
):
    """m2m abuse signal ingest (auto-block is staff-path; Nest posts signals here)."""
    from app.models import AbuseSignal

    tenant = None
    tenant_ref = str(body.get("tenant_ref") or body.get("tenantId") or "")
    if tenant_ref:
        tenant = db.execute(
            select(Tenant).where(
                (Tenant.external_id == tenant_ref) | (Tenant.slug == tenant_ref)
            )
        ).scalar_one_or_none()

    signal = AbuseSignal(
        tenant_id=tenant.id if tenant else None,
        signal_type=str(
            body.get("signal_type") or body.get("signalType") or "api_abuse"
        ),
        value=str(body.get("value") or ""),
        score=int(body.get("score") or 1),
        evidence=str(body.get("evidence") or ""),
    )
    db.add(signal)
    record_audit(
        db,
        action="abuse.signal.m2m",
        actor_type="service",
        actor_id=svc.client.client_id,
        target_type="abuse_signal",
        after=body if isinstance(body, dict) else None,
    )
    db.commit()
    db.refresh(signal)
    return {"accepted": True, "signalId": signal.id}
