"""Custom / negotiated school deals (Phase B).

Deals are `Subscription` rows with type=custom plus deal_name / pricing /
entitlements. Catalog subscriptions stay under /v1/tenants/{id}/subscriptions.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import bump_config_version, record_audit
from app.database import get_db
from app.deps import StaffContext, require_permissions
from app.models import DealTemplate, Subscription, Tenant
from app.schemas import (
    DealCloneFromTemplate,
    DealCreate,
    DealTemplateCreate,
    DealTemplateOut,
    DealUpdate,
    SubscriptionOut,
)
from app.services.billing import (
    _add_months,
    advance_next_invoice_at,
    create_invoice_for_subscription,
    set_next_invoice_at,
)

router = APIRouter(prefix="/v1/deals", tags=["deals"])


def _build_term_schedule(body, billing_cycle: str) -> dict | None:
    """Build one explicit schedule for termly billing, if configured."""
    explicit = body.term_start_dates
    interval = body.term_interval_months
    count = body.term_count
    if not explicit and interval is None and count == 3:
        return None
    if billing_cycle != "termly":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Term dates and term intervals are only valid for termly billing",
        )
    if explicit and interval is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Choose explicit term dates or a start date with an interval",
        )
    if explicit:
        if len(explicit) != count:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Provide a start date for all {count} terms",
            )
        if len(explicit) > 24 or any(
            right <= left for left, right in zip(explicit, explicit[1:])
        ):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Term start dates must be in strictly increasing order",
            )
        return {"dates": [value.isoformat() for value in explicit]}
    if body.contract_start is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A first term start date is required when using an interval",
        )
    months = interval or 3
    return {
        "dates": [
            _add_months(body.contract_start, months * index).isoformat()
            for index in range(count)
        ],
        "intervalMonths": months,
    }

APPROVAL_STATUSES = ("draft", "pending", "approved", "active")


@router.get("", response_model=list[SubscriptionOut])
def list_deals(
    _: StaffContext = Depends(require_permissions("deals:read")),
    db: Session = Depends(get_db),
):
    return db.execute(
        select(Subscription)
        .where(Subscription.type == "custom")
        .order_by(Subscription.id.desc())
    ).scalars().all()


@router.get("/templates", response_model=list[DealTemplateOut])
def list_deal_templates(
    active_only: bool = True,
    _: StaffContext = Depends(require_permissions("deals:read")),
    db: Session = Depends(get_db),
):
    stmt = select(DealTemplate).order_by(DealTemplate.name)
    if active_only:
        stmt = stmt.where(DealTemplate.is_active.is_(True))
    return db.execute(stmt).scalars().all()


@router.post("/templates", response_model=DealTemplateOut, status_code=status.HTTP_201_CREATED)
def create_deal_template(
    body: DealTemplateCreate,
    staff: StaffContext = Depends(require_permissions("deals:write")),
    db: Session = Depends(get_db),
):
    row = DealTemplate(
        name=body.name,
        description=body.description,
        price_minor=body.price_minor,
        currency=body.currency,
        billing_cycle=body.billing_cycle,
        pricing_model=body.pricing_model or "flat",
        per_student_minor=body.per_student_minor,
        student_count=body.student_count,
        entitlements=body.entitlements,
        quotas=body.quotas,
        is_active=True,
    )
    db.add(row)
    record_audit(
        db,
        action="deal_template.create",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="deal_template",
        target_id=body.name,
        after=body.model_dump(),
    )
    db.commit()
    db.refresh(row)
    return row


@router.post("/templates/{template_id}/clone", response_model=SubscriptionOut, status_code=status.HTTP_201_CREATED)
def clone_template_to_deal(
    template_id: int,
    body: DealCloneFromTemplate,
    staff: StaffContext = Depends(require_permissions("deals:write")),
    db: Session = Depends(get_db),
):
    template = db.get(DealTemplate, template_id)
    if template is None or not template.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deal template not found")

    tenant = db.get(Tenant, body.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    approval = "pending" if body.submit_for_approval else "draft"
    term_schedule = _build_term_schedule(body, template.billing_cycle)
    contract_start = (
        body.contract_start
        or (body.term_start_dates[0] if body.term_start_dates else None)
    )
    student_count = body.student_count if body.student_count is not None else template.student_count
    price_minor = template.price_minor
    if (template.pricing_model or "flat") == "per_student":
        rate = int(template.per_student_minor or 0)
        count = int(student_count or 0)
        price_minor = rate * count if rate and count else template.price_minor

    deal = Subscription(
        tenant_id=tenant.id,
        type="custom",
        plan_id=None,
        status="trialing" if approval != "active" else "active",
        billing_cycle=template.billing_cycle,
        price_minor=price_minor,
        currency=template.currency,
        pricing_model=template.pricing_model or "flat",
        per_student_minor=template.per_student_minor,
        student_count=student_count,
        custom_entitlements=template.entitlements or {},
        custom_quotas=template.quotas or {},
        notes=body.notes or f"Cloned from template #{template.id}: {template.name}",
        deal_name=body.deal_name or template.name,
        is_current=False,
        approval_status=approval,
        contract_start=contract_start,
        billing_schedule=term_schedule,
    )
    db.add(deal)
    db.flush()
    if deal.contract_start:
        deal.next_invoice_at = deal.contract_start
    else:
        set_next_invoice_at(deal)

    if body.create_invoice and deal.approval_status == "active":
        invoice = create_invoice_for_subscription(
            db, subscription=deal, staff_email=staff.email
        )
        if invoice is not None:
            advance_next_invoice_at(deal, invoice.period_start)

    record_audit(
        db,
        action="deal.clone_from_template",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="deal",
        target_id=str(deal.id),
        after={"template_id": template_id, "tenant_id": tenant.id, "approval_status": approval},
    )
    bump_config_version(db)
    db.commit()
    db.refresh(deal)
    return deal


@router.get("/{deal_id}", response_model=SubscriptionOut)
def get_deal(
    deal_id: int,
    _: StaffContext = Depends(require_permissions("deals:read")),
    db: Session = Depends(get_db),
):
    deal = db.get(Subscription, deal_id)
    if deal is None or deal.type != "custom":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deal not found")
    return deal


@router.get("/{deal_id}/entitlement-preview")
def entitlement_preview(
    deal_id: int,
    _: StaffContext = Depends(require_permissions("deals:read")),
    db: Session = Depends(get_db),
):
    from app.catalog import FLAG_CATALOG

    deal = db.get(Subscription, deal_id)
    if deal is None or deal.type != "custom":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deal not found")
    entitlements = dict(deal.custom_entitlements or {})
    quotas = dict(deal.custom_quotas or {})
    flags = []
    for key, name, default_enabled, min_plan, _providers in FLAG_CATALOG:
        if key in entitlements:
            enabled = bool(entitlements[key])
            source = "deal"
        elif default_enabled:
            enabled = True
            source = "foundation"
        else:
            enabled = False
            source = "off"
        flags.append(
            {
                "key": key,
                "name": name,
                "enabled": enabled,
                "source": source,
                "minPlan": min_plan,
            }
        )
    enabled_count = sum(1 for row in flags if row["enabled"])
    return {
        "dealId": deal.id,
        "tenantId": deal.tenant_id,
        "dealName": deal.deal_name,
        "approvalStatus": deal.approval_status,
        "quotas": quotas,
        "enabledCount": enabled_count,
        "flagCount": len(flags),
        "flags": flags,
    }


@router.post("/{deal_id}/approve", response_model=SubscriptionOut)
def approve_deal(
    deal_id: int,
    staff: StaffContext = Depends(require_permissions("deals:write")),
    db: Session = Depends(get_db),
):
    deal = db.get(Subscription, deal_id)
    if deal is None or deal.type != "custom":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deal not found")
    if deal.approval_status not in ("draft", "pending"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Deal approval_status is '{deal.approval_status}' — only draft/pending can be approved",
        )

    before = {"approval_status": deal.approval_status, "status": deal.status}
    deal.approval_status = "approved"
    # Approval authorizes the commercial terms; access is activated only after
    # the first SaaS invoice is settled through the payment gateway.
    deal.status = "past_due"
    for existing in db.execute(
        select(Subscription).where(
            Subscription.tenant_id == deal.tenant_id,
            Subscription.is_current.is_(True),
            Subscription.id != deal.id,
        )
    ).scalars():
        existing.is_current = False
    deal.is_current = True
    if deal.next_invoice_at is None:
        set_next_invoice_at(deal)
    # Approval opens the first invoice immediately so the tenant has a concrete
    # billing record to pay instead of waiting for the next billing-worker run.
    if deal.next_invoice_at is None or deal.next_invoice_at <= datetime.now(timezone.utc):
        invoice = create_invoice_for_subscription(
            db,
            subscription=deal,
            staff_email=staff.email,
            period_start=deal.contract_start or datetime.now(timezone.utc),
        )
        if invoice is not None:
            advance_next_invoice_at(deal, invoice.period_start)

    record_audit(
        db,
        action="deal.approve",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="deal",
        target_id=str(deal_id),
        before=before,
        after={"approval_status": deal.approval_status, "status": deal.status},
    )
    bump_config_version(db)
    db.commit()
    db.refresh(deal)
    return deal


@router.post("", response_model=SubscriptionOut, status_code=status.HTTP_201_CREATED)
def create_deal(
    body: DealCreate,
    staff: StaffContext = Depends(require_permissions("deals:write")),
    db: Session = Depends(get_db),
):
    tenant = db.get(Tenant, body.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    deal = Subscription(
        tenant_id=tenant.id,
        type="custom",
        plan_id=None,
        status="trialing",
        billing_cycle=body.billing_cycle,
        price_minor=(
            (body.per_student_minor or 0) * (body.student_count or 0)
            if (body.pricing_model or "flat") == "per_student"
            and body.per_student_minor
            and body.student_count
            else body.price_minor
        ),
        currency=body.currency,
        pricing_model=body.pricing_model or "flat",
        per_student_minor=body.per_student_minor,
        student_count=body.student_count,
        custom_entitlements=body.custom_entitlements,
        custom_quotas=body.custom_quotas,
        contract_start=(
            body.contract_start
            or (body.term_start_dates[0] if body.term_start_dates else None)
        ),
        contract_end=body.contract_end,
        billing_schedule=_build_term_schedule(body, body.billing_cycle),
        notes=body.notes,
        deal_name=body.deal_name,
        is_current=False,
        approval_status="pending",
    )
    db.add(deal)
    db.flush()
    if deal.contract_start:
        deal.next_invoice_at = deal.contract_start
    else:
        set_next_invoice_at(deal)

    if body.create_invoice and (
        deal.next_invoice_at is None
        or deal.next_invoice_at <= datetime.now(timezone.utc)
    ):
        invoice = create_invoice_for_subscription(
            db, subscription=deal, staff_email=staff.email
        )
        if invoice is not None:
            advance_next_invoice_at(deal, invoice.period_start)

    record_audit(
        db,
        action="deal.create",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="deal",
        target_id=str(deal.id),
        after={
            "tenant_id": tenant.id,
            "deal_name": body.deal_name,
            "price_minor": body.price_minor,
            "entitlements": body.custom_entitlements,
            "approval_status": deal.approval_status,
        },
    )
    bump_config_version(db)
    db.commit()
    db.refresh(deal)
    return deal


@router.patch("/{deal_id}", response_model=SubscriptionOut)
def update_deal(
    deal_id: int,
    body: DealUpdate,
    staff: StaffContext = Depends(require_permissions("deals:write")),
    db: Session = Depends(get_db),
):
    deal = db.get(Subscription, deal_id)
    if deal is None or deal.type != "custom":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deal not found")
    if body.status == "active" or body.approval_status in ("approved", "active"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Use the approval endpoint to activate a deal",
        )

    before = {
        "deal_name": deal.deal_name,
        "price_minor": deal.price_minor,
        "pricing_model": deal.pricing_model,
        "per_student_minor": deal.per_student_minor,
        "student_count": deal.student_count,
        "status": deal.status,
        "approval_status": deal.approval_status,
        "custom_entitlements": deal.custom_entitlements,
        "custom_quotas": deal.custom_quotas,
        "notes": deal.notes,
    }

    commercial_change = any(
        getattr(body, field) is not None
        for field in (
            "billing_cycle",
            "price_minor",
            "currency",
            "pricing_model",
            "per_student_minor",
            "student_count",
            "custom_entitlements",
            "custom_quotas",
            "contract_start",
            "contract_end",
            "term_start_dates",
            "term_interval_months",
            "term_count",
        )
    )
    for field in (
        "deal_name",
        "billing_cycle",
        "price_minor",
        "currency",
        "pricing_model",
        "per_student_minor",
        "student_count",
        "custom_entitlements",
        "custom_quotas",
        "contract_start",
        "contract_end",
        "notes",
        "status",
        "approval_status",
    ):
        val = getattr(body, field)
        if val is not None:
            if field == "approval_status" and val not in APPROVAL_STATUSES:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid approval_status: {val}")
            if field == "pricing_model" and val not in ("flat", "per_student"):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid pricing_model: {val}")
            setattr(deal, field, val)

    if (
        body.term_start_dates is not None
        or body.term_interval_months is not None
        or body.term_count is not None
    ):
        schedule_body = body.model_copy(
            update={
                "contract_start": body.contract_start or deal.contract_start,
                "term_count": body.term_count
                or len(body.term_start_dates or [])
                or 3,
            }
        )
        deal.billing_schedule = _build_term_schedule(
            schedule_body, deal.billing_cycle
        )
        if body.term_start_dates:
            deal.contract_start = body.term_start_dates[0]
        elif body.contract_start:
            deal.contract_start = body.contract_start
        deal.next_invoice_at = deal.contract_start

    # Keep cached period total in sync for per-student deals.
    if (deal.pricing_model or "flat") == "per_student" and deal.per_student_minor and deal.student_count:
        deal.price_minor = int(deal.per_student_minor) * int(deal.student_count)
    if commercial_change and deal.approval_status in ("approved", "active"):
        deal.approval_status = "pending"
        deal.status = "trialing"
        deal.is_current = False

    record_audit(
        db,
        action="deal.update",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="deal",
        target_id=str(deal_id),
        reason=body.reason,
        before=before,
        after={
            "deal_name": deal.deal_name,
            "price_minor": deal.price_minor,
            "pricing_model": deal.pricing_model,
            "per_student_minor": deal.per_student_minor,
            "student_count": deal.student_count,
            "status": deal.status,
            "approval_status": deal.approval_status,
            "custom_entitlements": deal.custom_entitlements,
            "custom_quotas": deal.custom_quotas,
            "notes": deal.notes,
        },
    )
    bump_config_version(db)
    db.commit()
    db.refresh(deal)
    return deal
