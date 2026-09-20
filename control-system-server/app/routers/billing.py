"""SaaS billing invoices + dunning (Phase B)."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import record_audit
from app.config import get_settings
from app.database import get_db
from app.deps import StaffContext, require_permissions
from app.models import SaasInvoice, SaasPayment, Subscription, Tenant
from app.schemas import (
    CourtesyUnlockRequest,
    SaasInvoiceCreate,
    SaasInvoiceOut,
    SaasPaymentCreate,
    SaasPaymentOut,
    SubscriptionOut,
    SubscriptionStatusUpdate,
)
from app.services.billing import (
    advance_dunning,
    advance_next_invoice_at,
    apply_payment_to_invoice,
    create_invoice_for_subscription,
    estimate_mrr,
    generate_due_invoices,
    run_dunning_pass,
    settle_invoice,
)
from app.services.invoice_pdf import build_invoice_pdf

router = APIRouter(prefix="/v1", tags=["billing"])


@router.get("/subscriptions", response_model=list[SubscriptionOut])
def list_all_subscriptions(
    current_only: bool = Query(default=True),
    _: StaffContext = Depends(require_permissions("subscriptions:read")),
    db: Session = Depends(get_db),
):
    stmt = select(Subscription).order_by(Subscription.id.desc())
    if current_only:
        stmt = stmt.where(Subscription.is_current.is_(True))
    return db.execute(stmt).scalars().all()


@router.patch("/subscriptions/{subscription_id}", response_model=SubscriptionOut)
def update_subscription_status(
    subscription_id: int,
    body: SubscriptionStatusUpdate,
    staff: StaffContext = Depends(require_permissions("subscriptions:write")),
    db: Session = Depends(get_db),
):
    sub = db.get(Subscription, subscription_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")
    before = {"status": sub.status, "dunning_step": sub.dunning_step}
    if body.status is not None:
        sub.status = body.status
    if body.dunning_step is not None:
        sub.dunning_step = body.dunning_step
    if body.status == "active":
        sub.dunning_step = "none"
        sub.past_due_at = None
        sub.grace_until = None
    record_audit(
        db,
        action="subscription.update",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="subscription",
        target_id=str(subscription_id),
        reason=body.reason,
        before=before,
        after={"status": sub.status, "dunning_step": sub.dunning_step},
    )
    db.commit()
    db.refresh(sub)
    return sub


@router.post(
    "/subscriptions/{subscription_id}/dunning/advance",
    response_model=dict,
)
def dunning_advance(
    subscription_id: int,
    staff: StaffContext = Depends(require_permissions("billing:write")),
    db: Session = Depends(get_db),
):
    sub = db.get(Subscription, subscription_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")
    result = advance_dunning(db, sub, staff_email=staff.email)
    db.commit()
    return result


@router.post("/billing/dunning/run")
def dunning_run(
    staff: StaffContext = Depends(require_permissions("billing:write")),
    db: Session = Depends(get_db),
):
    results = run_dunning_pass(db)
    record_audit(
        db,
        action="billing.dunning.run",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="billing",
        after={"advanced": len(results)},
    )
    db.commit()
    return {"advanced": results, "count": len(results)}


@router.post("/billing/invoices/generate-due")
def generate_due(
    staff: StaffContext = Depends(require_permissions("billing:write")),
    db: Session = Depends(get_db),
):
    invoices = generate_due_invoices(db, staff_email=staff.email)
    record_audit(
        db,
        action="billing.invoices.generate_due",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="billing",
        after={"created": len(invoices), "numbers": [i.number for i in invoices]},
    )
    db.commit()
    return {
        "created": len(invoices),
        "invoices": [{"id": i.id, "number": i.number, "tenantId": i.tenant_id} for i in invoices],
    }


@router.post("/billing/courtesy-unlock")
def courtesy_unlock(
    body: CourtesyUnlockRequest,
    staff: StaffContext = Depends(require_permissions("billing:write")),
    db: Session = Depends(get_db),
):
    from datetime import datetime, timedelta, timezone

    from app.audit import bump_config_version
    from app.services.outbox import enqueue_outbox

    tenant = db.get(Tenant, body.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    if not body.reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Reason required")

    until = datetime.now(timezone.utc) + timedelta(hours=body.duration_hours)
    before = {"status": tenant.status, "courtesy_unlock_until": None}
    tenant.status = "active"
    tenant.status_message = f"Courtesy unlock until {until.isoformat()}"
    tenant.courtesy_unlock_until = until

    sub = db.execute(
        select(Subscription)
        .where(Subscription.tenant_id == tenant.id, Subscription.is_current.is_(True))
        .order_by(Subscription.id.desc())
    ).scalars().first()
    if sub is not None:
        sub.dunning_step = "none"
        sub.past_due_at = None
        sub.grace_until = None

    record_audit(
        db,
        action="billing.courtesy_unlock",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="tenant",
        target_id=str(tenant.id),
        reason=body.reason,
        before=before,
        after={"status": tenant.status, "courtesy_unlock_until": until.isoformat()},
    )
    bump_config_version(db)
    enqueue_outbox(
        db,
        event_type="config.invalidate",
        aggregate_type="tenant",
        aggregate_id=str(tenant.id),
        payload={"reason": "courtesy_unlock", "until": until.isoformat()},
    )
    db.commit()
    return {"tenantId": tenant.id, "courtesyUnlockUntil": until.isoformat(), "status": tenant.status}


@router.get("/billing/mrr")
def mrr_estimate(
    _: StaffContext = Depends(require_permissions("billing:read")),
    db: Session = Depends(get_db),
):
    return estimate_mrr(db)


@router.get("/billing/invoices", response_model=list[SaasInvoiceOut])
def list_invoices(
    tenant_id: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    _: StaffContext = Depends(require_permissions("billing:read")),
    db: Session = Depends(get_db),
):
    stmt = select(SaasInvoice).order_by(SaasInvoice.id.desc())
    if tenant_id is not None:
        stmt = stmt.where(SaasInvoice.tenant_id == tenant_id)
    if status_filter:
        stmt = stmt.where(SaasInvoice.status == status_filter)
    return db.execute(stmt.limit(200)).scalars().all()


@router.post(
    "/billing/invoices",
    response_model=SaasInvoiceOut,
    status_code=status.HTTP_201_CREATED,
)
def create_invoice(
    body: SaasInvoiceCreate,
    staff: StaffContext = Depends(require_permissions("billing:write")),
    db: Session = Depends(get_db),
):
    tenant = db.get(Tenant, body.tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    sub = None
    if body.subscription_id:
        sub = db.get(Subscription, body.subscription_id)
    else:
        sub = db.execute(
            select(Subscription)
            .where(Subscription.tenant_id == body.tenant_id, Subscription.is_current.is_(True))
            .order_by(Subscription.id.desc())
        ).scalars().first()

    if sub is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No subscription to invoice")

    invoice = create_invoice_for_subscription(
        db, subscription=sub, staff_email=staff.email, due_days=body.due_days
    )
    if invoice is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "An invoice already exists for this subscription's current billing period",
        )

    if body.amount_minor is not None:
        invoice.amount_minor = body.amount_minor
    if body.currency:
        invoice.currency = body.currency
    if body.notes:
        invoice.notes = body.notes
    advance_next_invoice_at(sub, invoice.period_start)

    # amount_minor is the gross total, so tax has to be folded in rather than
    # recorded alongside it — otherwise the PDF showed VAT the invoice never charged.
    invoice.tax_label = body.tax_label or "VAT"
    invoice.tax_minor = max(0, body.tax_minor or 0)
    if invoice.tax_minor:
        invoice.amount_minor = invoice.amount_minor + invoice.tax_minor

    db.commit()
    db.refresh(invoice)
    return invoice


@router.get("/billing/invoices/{invoice_id}/pdf")
def invoice_pdf(
    invoice_id: int,
    staff: StaffContext = Depends(require_permissions("billing:read")),
    db: Session = Depends(get_db),
):
    invoice = db.get(SaasInvoice, invoice_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    tenant = db.get(Tenant, invoice.tenant_id)
    pdf = build_invoice_pdf(
        issuer=get_settings().invoice_issuer_name,
        invoice_number=invoice.number,
        tenant_name=tenant.name if tenant else f"Tenant {invoice.tenant_id}",
        tenant_slug=tenant.slug if tenant else "",
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
    record_audit(
        db,
        action="billing.invoice.pdf",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="saas_invoice",
        target_id=str(invoice_id),
    )
    db.commit()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{invoice.number}.pdf"',
        },
    )


@router.post(
    "/billing/invoices/{invoice_id}/pay",
    response_model=SaasInvoiceOut,
)
def pay_invoice(
    invoice_id: int,
    body: SaasPaymentCreate,
    staff: StaffContext = Depends(require_permissions("billing:write")),
    db: Session = Depends(get_db),
):
    invoice = db.get(SaasInvoice, invoice_id)
    if invoice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    if invoice.status in ("void", "written_off"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Invoice is {invoice.status} and cannot accept payments",
        )
    if body.amount_minor <= 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Amount must be positive")
    outstanding = max(0, int(invoice.amount_minor or 0) - int(invoice.amount_paid_minor or 0))
    if body.amount_minor > outstanding:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Payment exceeds the invoice balance",
        )

    # A retried gateway webhook carrying the same reference must not double-credit.
    if body.provider_reference:
        duplicate = db.execute(
            select(SaasPayment).where(SaasPayment.provider_reference == body.provider_reference)
        ).scalars().first()
        if duplicate is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Payment reference {body.provider_reference} is already recorded",
            )

    payment = SaasPayment(
        invoice_id=invoice.id,
        tenant_id=invoice.tenant_id,
        amount_minor=body.amount_minor,
        currency=invoice.currency,
        method=body.method,
        provider_id=body.provider_id,
        provider_reference=body.provider_reference,
        recorded_by=staff.email,
        notes=body.notes,
    )
    db.add(payment)
    try:
        apply_payment_to_invoice(db, invoice, body.amount_minor, staff_email=staff.email)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    db.commit()
    db.refresh(invoice)
    return invoice


@router.get(
    "/billing/invoices/{invoice_id}/payments",
    response_model=list[SaasPaymentOut],
)
def list_invoice_payments(
    invoice_id: int,
    _: StaffContext = Depends(require_permissions("billing:read")),
    db: Session = Depends(get_db),
):
    return db.execute(
        select(SaasPayment)
        .where(SaasPayment.invoice_id == invoice_id)
        .order_by(SaasPayment.id.desc())
    ).scalars().all()
