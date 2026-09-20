"""SaaS invoice creation + dunning state machine (Phase B).

Flow: active → past_due → grace → suspend (tenant.status=suspended).
"""

import logging
from datetime import datetime, timedelta, timezone
from calendar import monthrange

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import bump_config_version, record_audit
from app.models import Plan, SaasInvoice, Subscription, Tenant
from app.services.outbox import enqueue_outbox

logger = logging.getLogger("control.billing")

DEFAULT_GRACE_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


BILLING_CYCLES = ("monthly", "termly", "yearly")

# A subscription this far behind is a data problem, not a backlog — stop rather
# than emit an unbounded run of invoices.
MAX_CATCHUP_PERIODS = 24


def normalize_cycle(cycle: str | None) -> str:
    value = (cycle or "monthly").strip().lower()
    return value if value in BILLING_CYCLES else "monthly"


def _add_months(
    when: datetime, months: int, *, anchor_day: int | None = None
) -> datetime:
    """Calendar-correct month arithmetic that keeps the billing anchor day.

    Using timedelta(days=30) billed 12.17 times a year and walked the anniversary
    date backwards every cycle.
    """
    total = when.month - 1 + months
    year = when.year + total // 12
    month = total % 12 + 1
    day = min(anchor_day or when.day, monthrange(year, month)[1])
    return datetime(
        year,
        month,
        day,
        when.hour,
        when.minute,
        when.second,
        tzinfo=when.tzinfo or timezone.utc,
    )


def _cycle_months(cycle: str) -> int:
    return {"monthly": 1, "termly": 3, "yearly": 12}[normalize_cycle(cycle)]


def _scheduled_dates(subscription: Subscription) -> list[datetime]:
    raw_dates = (subscription.billing_schedule or {}).get("dates", [])
    dates: list[datetime] = []
    for raw in raw_dates:
        value = datetime.fromisoformat(raw)
        dates.append(value if value.tzinfo else value.replace(tzinfo=timezone.utc))
    return dates


def _next_scheduled_date(
    subscription: Subscription, after: datetime
) -> datetime | None:
    return next((date for date in _scheduled_dates(subscription) if date > after), None)


def _invoice_number(tenant_id: int, period_key: str, subscription_id: int) -> str:
    """Deterministic and collision-free. The old format used HHMMSS, so two
    invoices in the same second raised IntegrityError and aborted the whole batch."""
    return f"SAAS-{tenant_id}-{period_key}-{subscription_id}"


def period_key_for(subscription: Subscription, period_start: datetime) -> str:
    if subscription.billing_schedule is not None:
        return period_start.strftime("%Y-%m-%d")
    cycle = normalize_cycle(subscription.billing_cycle)
    if cycle == "yearly":
        return f"{period_start.year:04d}"
    return f"{period_start.year:04d}-{period_start.month:02d}"


def period_bounds(
    subscription: Subscription, period_start: datetime
) -> tuple[datetime, datetime]:
    """The service window this invoice actually covers, anchored on the
    subscription's own billing date rather than the calendar month."""
    scheduled_next = _next_scheduled_date(subscription, period_start)
    if scheduled_next is not None:
        period_end = scheduled_next - timedelta(seconds=1)
    else:
        months = _cycle_months(subscription.billing_cycle)
        period_end = _add_months(period_start, months) - timedelta(seconds=1)
    return period_start, period_end


def _next_period_start(
    subscription: Subscription, from_when: datetime | None = None
) -> datetime:
    now = from_when or _now()
    scheduled_next = _next_scheduled_date(subscription, now)
    if subscription.billing_schedule is not None:
        return scheduled_next or now
    anchor = subscription.next_invoice_at or now
    anchor_day = anchor.day if subscription.next_invoice_at else now.day
    return _add_months(
        now, _cycle_months(subscription.billing_cycle), anchor_day=anchor_day
    )


def set_next_invoice_at(
    subscription: Subscription, from_when: datetime | None = None
) -> None:
    subscription.next_invoice_at = _next_period_start(subscription, from_when)


def advance_next_invoice_at(
    subscription: Subscription, from_when: datetime
) -> None:
    """Move the displayed next billing date past the invoice just created."""
    if subscription.billing_schedule is not None:
        subscription.next_invoice_at = _next_scheduled_date(subscription, from_when)
    else:
        subscription.next_invoice_at = _add_months(
            from_when,
            _cycle_months(subscription.billing_cycle),
            anchor_day=from_when.day,
        )


def _prefetch_plans(db: Session, subscriptions) -> None:
    """Warm the identity map so later db.get(Plan, id) calls are free."""
    plan_ids = {s.plan_id for s in subscriptions if s.plan_id}
    if plan_ids:
        db.execute(select(Plan).where(Plan.id.in_(plan_ids))).scalars().all()


def resolve_period_amount_minor(subscription: Subscription, db: Session) -> int:
    """Billable amount for the current period (flat or per-student)."""
    pricing = (subscription.pricing_model or "flat").strip().lower()
    if pricing == "per_student":
        rate = int(subscription.per_student_minor or 0)
        count = int(subscription.student_count or 0)
        if rate > 0 and count > 0:
            return rate * count
        # Fall through if incomplete — use cached price_minor if set
        if subscription.price_minor:
            return int(subscription.price_minor)
        return 0

    amount = subscription.price_minor
    if amount is None and subscription.plan_id:
        plan = db.get(Plan, subscription.plan_id)
        amount = plan.monthly_price_minor if plan else 0
        if subscription.billing_cycle == "yearly" and plan:
            amount = plan.monthly_price_minor * 12
    return int(amount or 0)


def monthly_recurring_minor(subscription: Subscription, db: Session) -> int:
    """Normalize subscription price to monthly minor units for MRR. Rounds to
    nearest rather than flooring, which systematically under-reported."""
    amount = resolve_period_amount_minor(subscription, db)
    months = _cycle_months(subscription.billing_cycle)
    if months == 1:
        return amount
    return (amount + months // 2) // months


def estimate_mrr(db: Session) -> dict:
    subs = (
        db.execute(
            select(Subscription).where(
                Subscription.is_current.is_(True),
                Subscription.status.in_(("active", "trialing")),
            )
        )
        .scalars()
        .all()
    )
    # Plans are prefetched so monthly_recurring_minor does not issue a query per
    # subscription (it was called twice per row below).
    _prefetch_plans(db, subs)
    by_type: dict[str, int] = {}
    by_pricing: dict[str, int] = {}
    total = 0
    for s in subs:
        m = monthly_recurring_minor(s, db)
        total += m
        by_type[s.type] = by_type.get(s.type, 0) + m
        model = (s.pricing_model or "flat").strip().lower()
        by_pricing[model] = by_pricing.get(model, 0) + m
    return {
        "mrrMinor": total,
        "currency": "NGN",
        "byType": by_type,
        "byPricingModel": by_pricing,
        "activeSubscriptions": len(subs),
    }


def revenue_trajectory(db: Session, *, months: int = 12) -> dict:
    """Monthly invoice/payment aggregates for business trajectory charts."""
    from app.models import SaasPayment

    months = max(3, min(months, 24))
    now = _now()
    # Build month buckets oldest → newest
    buckets: list[dict] = []
    y, m = now.year, now.month
    for _ in range(months):
        buckets.append({"year": y, "month": m, "key": f"{y:04d}-{m:02d}"})
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    buckets.reverse()
    start = datetime(buckets[0]["year"], buckets[0]["month"], 1, tzinfo=timezone.utc)

    invoiced: dict[str, int] = {b["key"]: 0 for b in buckets}
    open_due: dict[str, int] = {b["key"]: 0 for b in buckets}

    # Aggregate in SQL rather than loading every invoice and payment of the last
    # 12 months into Python.
    invoice_month = (
        func.strftime("%Y-%m", SaasInvoice.created_at)
        if db.bind is not None and db.bind.dialect.name == "sqlite"
        else func.to_char(SaasInvoice.created_at, "YYYY-MM")
    )
    invoice_rows = db.execute(
        select(
            invoice_month,
            SaasInvoice.status,
            func.sum(SaasInvoice.amount_minor),
        )
        .where(SaasInvoice.created_at >= start)
        .group_by(invoice_month, SaasInvoice.status)
    ).all()
    for key, status, amount in invoice_rows:
        if key not in invoiced:
            continue
        invoiced[key] += int(amount or 0)
        if status in ("open", "past_due", "partially_paid"):
            open_due[key] += int(amount or 0)

    payment_month = (
        func.strftime("%Y-%m", SaasPayment.created_at)
        if db.bind is not None and db.bind.dialect.name == "sqlite"
        else func.to_char(SaasPayment.created_at, "YYYY-MM")
    )
    payment_rows = db.execute(
        select(
            payment_month,
            func.sum(SaasPayment.amount_minor),
        )
        .where(SaasPayment.created_at >= start)
        .group_by(payment_month)
    ).all()
    payment_series: dict[str, int] = {b["key"]: 0 for b in buckets}
    for key, amount in payment_rows:
        if key in payment_series:
            payment_series[key] += int(amount or 0)

    series = []
    for b in buckets:
        key = b["key"]
        series.append(
            {
                "month": key,
                "label": datetime(b["year"], b["month"], 1).strftime("%b %Y"),
                "invoicedMinor": invoiced[key],
                # Cash actually received. This used to fall back from
                # invoice-derived to payment-derived figures per bucket, so the
                # total summed two incompatible sources.
                "collectedMinor": payment_series[key],
                "openMinor": open_due[key],
                "paymentsMinor": payment_series[key],
            }
        )

    total_invoiced = sum(p["invoicedMinor"] for p in series)
    total_collected = sum(p["collectedMinor"] for p in series)
    total_open = sum(p["openMinor"] for p in series)
    # Simple MoM growth on collected
    growth_pct = None
    if len(series) >= 2 and series[-2]["collectedMinor"] > 0:
        growth_pct = round(
            (
                (series[-1]["collectedMinor"] - series[-2]["collectedMinor"])
                / series[-2]["collectedMinor"]
            )
            * 100,
            1,
        )

    return {
        "currency": "NGN",
        "months": months,
        "series": series,
        "totals": {
            "invoicedMinor": total_invoiced,
            "collectedMinor": total_collected,
            "openMinor": total_open,
        },
        "momCollectedGrowthPct": growth_pct,
    }


def generate_due_invoices(
    db: Session, *, staff_email: str = "system"
) -> list[SaasInvoice]:
    """Create invoices for every period a subscription has fallen behind on.

    Previously this emitted one invoice and jumped next_invoice_at to now + 30
    days, silently forgiving every missed period. Each subscription row is locked
    for the duration so concurrent replicas cannot double-bill.
    """
    now = _now()
    created: list[SaasInvoice] = []
    candidates = (
        db.execute(
            select(Subscription.id).where(
                Subscription.is_current.is_(True),
                Subscription.status.in_(("active", "past_due")),
                Subscription.next_invoice_at.isnot(None),
                Subscription.next_invoice_at <= now,
            )
        )
        .scalars()
        .all()
    )

    for sub_id in candidates:
        try:
            sub = _lock_subscription(db, sub_id)
            if sub is None:
                continue
            if sub.approval_status not in ("active", "approved"):
                continue

            periods = 0
            while periods < MAX_CATCHUP_PERIODS:
                nia = sub.next_invoice_at
                if nia is None:
                    break
                if nia.tzinfo is None:
                    nia = nia.replace(tzinfo=timezone.utc)
                if nia > now:
                    break

                invoice = create_invoice_for_subscription(
                    db, subscription=sub, staff_email=staff_email, period_start=nia
                )
                if invoice is not None:
                    created.append(invoice)
                advance_next_invoice_at(sub, nia)
                periods += 1

            if periods >= MAX_CATCHUP_PERIODS:
                logger.error(
                    "Subscription %s hit the catch-up ceiling of %s periods — "
                    "next_invoice_at left at %s for manual review",
                    sub.id,
                    MAX_CATCHUP_PERIODS,
                    sub.next_invoice_at,
                )
            db.commit()
        except IntegrityError:
            # Another replica created the same period's invoice first.
            db.rollback()
            logger.info(
                "Invoice for subscription %s already generated elsewhere", sub_id
            )
        except Exception:
            # One bad subscription must not abort the whole batch.
            db.rollback()
            logger.exception("Invoice generation failed for subscription %s", sub_id)

    return created


def _lock_subscription(db: Session, subscription_id: int) -> Subscription | None:
    """SELECT ... FOR UPDATE where the backend supports it (Postgres). SQLite
    ignores row locks, which is acceptable for single-process local development."""
    stmt = select(Subscription).where(Subscription.id == subscription_id)
    if db.bind is not None and db.bind.dialect.name != "sqlite":
        stmt = stmt.with_for_update(skip_locked=True)
    return db.execute(stmt).scalar_one_or_none()


def create_invoice_for_subscription(
    db: Session,
    *,
    subscription: Subscription,
    staff_email: str = "system",
    due_days: int = 14,
    period_start: datetime | None = None,
) -> SaasInvoice | None:
    """Create an open SaaS invoice for one billing period.

    Returns None when that period is already invoiced, which makes the caller
    safe to retry.
    """
    now = _now()
    start = period_start or subscription.next_invoice_at or now
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    key = period_key_for(subscription, start)
    existing = db.execute(
        select(SaasInvoice).where(
            SaasInvoice.subscription_id == subscription.id,
            SaasInvoice.period_key == key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None

    amount = resolve_period_amount_minor(subscription, db)
    # Keep price_minor in sync for per-student deals so list UIs show the period total.
    if (
        subscription.pricing_model or "flat"
    ).strip().lower() == "per_student" and amount:
        subscription.price_minor = amount

    window_start, window_end = period_bounds(subscription, start)

    pricing_note = ""
    if (subscription.pricing_model or "flat").strip().lower() == "per_student":
        pricing_note = (
            f" (per-student: {subscription.per_student_minor or 0} × "
            f"{subscription.student_count or 0})"
        )

    invoice = SaasInvoice(
        tenant_id=subscription.tenant_id,
        subscription_id=subscription.id,
        number=_invoice_number(subscription.tenant_id, key, subscription.id),
        period_key=key,
        amount_minor=amount,
        amount_paid_minor=0,
        currency=subscription.currency or "NGN",
        status="open",
        period_start=window_start,
        period_end=window_end,
        due_at=now + timedelta(days=due_days),
        notes=f"Generated for subscription #{subscription.id}{pricing_note}",
    )
    db.add(invoice)
    db.flush()
    record_audit(
        db,
        action="billing.invoice.create",
        actor_email=staff_email,
        target_type="saas_invoice",
        target_id=invoice.number,
        after={
            "tenant_id": subscription.tenant_id,
            "amount_minor": amount,
            "period_key": key,
        },
    )
    if subscription.next_invoice_at is None:
        set_next_invoice_at(subscription)
    return invoice


def apply_payment_to_invoice(
    db: Session,
    invoice: SaasInvoice,
    amount_minor: int,
    *,
    staff_email: str = "system",
) -> dict:
    """Credit a payment and settle the invoice only when it is fully covered.

    This previously marked any invoice paid in full for any payment amount, so a
    ₦1 payment closed a ₦2,000,000 invoice and un-suspended the tenant.
    """
    if amount_minor <= 0:
        raise ValueError("Payment amount must be greater than zero")

    already = int(invoice.amount_paid_minor or 0)
    total = int(invoice.amount_minor or 0)
    invoice.amount_paid_minor = already + amount_minor
    outstanding = max(0, total - invoice.amount_paid_minor)

    if invoice.amount_paid_minor >= total:
        settle_invoice(db, invoice, staff_email=staff_email)
    else:
        invoice.status = "partially_paid"
        record_audit(
            db,
            action="billing.invoice.partial_payment",
            actor_email=staff_email,
            target_type="saas_invoice",
            target_id=str(invoice.id),
            after={
                "amount_paid_minor": invoice.amount_paid_minor,
                "outstanding_minor": outstanding,
            },
        )

    return {
        "invoiceId": invoice.id,
        "status": invoice.status,
        "amountMinor": total,
        "amountPaidMinor": invoice.amount_paid_minor,
        "outstandingMinor": outstanding,
        "settled": invoice.status == "paid",
        "overpaidMinor": max(0, invoice.amount_paid_minor - total),
    }


def settle_invoice(
    db: Session,
    invoice: SaasInvoice,
    *,
    staff_email: str = "system",
) -> SaasInvoice:
    """Mark an invoice fully paid and lift any billing-caused suspension."""
    invoice.status = "paid"
    invoice.paid_at = _now()
    if (
        invoice.amount_paid_minor is None
        or invoice.amount_paid_minor < invoice.amount_minor
    ):
        invoice.amount_paid_minor = invoice.amount_minor

    subscription_reactivated = False
    if invoice.subscription_id:
        sub = db.get(Subscription, invoice.subscription_id)
        if sub is not None:
            # Only clear dunning when nothing else is still outstanding.
            unpaid = (
                db.execute(
                    select(SaasInvoice).where(
                        SaasInvoice.subscription_id == sub.id,
                        SaasInvoice.id != invoice.id,
                        SaasInvoice.status.in_(("open", "past_due", "partially_paid")),
                    )
                )
                .scalars()
                .first()
            )
            if unpaid is None:
                sub.status = "active"
                sub.dunning_step = "none"
                sub.past_due_at = None
                sub.grace_until = None
                subscription_reactivated = True

    tenant = db.get(Tenant, invoice.tenant_id)
    if subscription_reactivated:
        bump_config_version(db)
        enqueue_outbox(
            db,
            event_type="config.invalidate",
            aggregate_type="tenant",
            aggregate_id=str(invoice.tenant_id),
            payload={"reason": "subscription_payment"},
        )
    # Keys off status_reason rather than searching status_message for "saas",
    # which broke whenever the dunning copy was reworded.
    if (
        tenant is not None
        and tenant.status == "suspended"
        and tenant.status_reason == "billing_dunning"
    ):
        still_owing = (
            db.execute(
                select(SaasInvoice).where(
                    SaasInvoice.tenant_id == tenant.id,
                    SaasInvoice.id != invoice.id,
                    SaasInvoice.status.in_(("open", "past_due", "partially_paid")),
                )
            )
            .scalars()
            .first()
        )
        if still_owing is None:
            tenant.status = "active"
            tenant.status_message = ""
            tenant.status_reason = ""
            if not subscription_reactivated:
                bump_config_version(db)
                enqueue_outbox(
                    db,
                    event_type="config.invalidate",
                    aggregate_type="tenant",
                    aggregate_id=str(tenant.id),
                    payload={"reason": "invoice_paid"},
                )

    record_audit(
        db,
        action="billing.invoice.paid",
        actor_email=staff_email,
        target_type="saas_invoice",
        target_id=str(invoice.id),
        after={"amount_paid_minor": invoice.amount_paid_minor},
    )
    return invoice


def mark_invoice_paid(
    db: Session,
    invoice: SaasInvoice,
    *,
    staff_email: str = "system",
) -> SaasInvoice:
    """Deprecated alias retained for callers that intend a full write-off."""
    return settle_invoice(db, invoice, staff_email=staff_email)


def advance_dunning(
    db: Session,
    subscription: Subscription,
    *,
    grace_days: int = DEFAULT_GRACE_DAYS,
    staff_email: str = "system",
) -> dict:
    """Advance one step: active/open overdue → past_due → grace → suspend."""
    now = _now()
    tenant = db.get(Tenant, subscription.tenant_id)
    if tenant is None:
        return {"ok": False, "reason": "tenant_missing"}

    before = {
        "status": subscription.status,
        "dunning_step": subscription.dunning_step,
        "tenant_status": tenant.status,
    }

    step = subscription.dunning_step or "none"

    if step == "none" or subscription.status == "active":
        subscription.status = "past_due"
        subscription.dunning_step = "past_due"
        subscription.past_due_at = now
        subscription.grace_until = now + timedelta(days=grace_days)
        # Mark open invoices past_due
        for inv in db.execute(
            select(SaasInvoice).where(
                SaasInvoice.subscription_id == subscription.id,
                SaasInvoice.status == "open",
            )
        ).scalars():
            inv.status = "past_due"
        action = "billing.dunning.past_due"
    elif step == "past_due":
        subscription.dunning_step = "grace"
        if subscription.grace_until is None:
            subscription.grace_until = now + timedelta(days=grace_days)
        action = "billing.dunning.grace"
    else:
        # grace or already suspended → suspend tenant
        subscription.dunning_step = "suspended"
        subscription.status = "past_due"
        tenant.status = "suspended"
        tenant.status_message = "Suspended for unpaid SaaS subscription (dunning)."
        tenant.status_reason = "billing_dunning"
        bump_config_version(db)
        enqueue_outbox(
            db,
            event_type="config.invalidate",
            aggregate_type="tenant",
            aggregate_id=str(tenant.id),
            payload={"reason": "dunning_suspend"},
        )
        action = "billing.dunning.suspend"

    record_audit(
        db,
        action=action,
        actor_email=staff_email,
        target_type="subscription",
        target_id=str(subscription.id),
        before=before,
        after={
            "status": subscription.status,
            "dunning_step": subscription.dunning_step,
            "tenant_status": tenant.status,
        },
    )
    return {
        "ok": True,
        "subscriptionId": subscription.id,
        "dunningStep": subscription.dunning_step,
        "tenantStatus": tenant.status,
    }


def _tenant_billing_resolved(db: Session, tenant: Tenant) -> bool:
    """True when the tenant has a healthy subscription and no open SaaS invoices."""
    valid_sub = db.execute(
        select(Subscription).where(
            Subscription.tenant_id == tenant.id,
            Subscription.is_current.is_(True),
            Subscription.status.in_(("active", "trialing")),
            Subscription.dunning_step == "none",
        )
    ).scalars().first()
    if valid_sub is None:
        return False
    open_invoice = db.execute(
        select(SaasInvoice).where(
            SaasInvoice.tenant_id == tenant.id,
            SaasInvoice.status.in_(("open", "past_due", "partially_paid")),
        )
    ).scalars().first()
    return open_invoice is None


def run_courtesy_unlock_expiry_pass(db: Session) -> int:
    """Re-suspend tenants whose courtesy unlock window has ended without payment."""
    now = _now()
    tenants = db.execute(
        select(Tenant).where(
            Tenant.courtesy_unlock_until.isnot(None),
            Tenant.courtesy_unlock_until <= now,
        )
    ).scalars().all()
    expired = 0
    for tenant in tenants:
        if _tenant_billing_resolved(db, tenant):
            tenant.courtesy_unlock_until = None
            tenant.status_message = ""
            if tenant.status_reason in (
                "billing_dunning",
                "billing_grace_expired",
                "courtesy_unlock_expired",
            ):
                tenant.status_reason = ""
            bump_config_version(db)
            enqueue_outbox(
                db,
                event_type="config.invalidate",
                aggregate_type="tenant",
                aggregate_id=str(tenant.id),
                payload={"reason": "courtesy_unlock_resolved"},
            )
            continue

        prior_reason = tenant.status_reason or "courtesy_unlock_expired"
        tenant.status = "suspended"
        tenant.courtesy_unlock_until = None

        if prior_reason == "billing_dunning":
            tenant.status_message = "Suspended for unpaid SaaS subscription (dunning)."
            tenant.status_reason = "billing_dunning"
        elif prior_reason == "billing_grace_expired":
            tenant.status_message = (
                "This school is suspended because it has no active subscription."
            )
            tenant.status_reason = "billing_grace_expired"
        else:
            tenant.status_message = (
                "Courtesy unlock expired. Please renew your subscription or contact support."
            )
            tenant.status_reason = "courtesy_unlock_expired"

        bump_config_version(db)
        enqueue_outbox(
            db,
            event_type="config.invalidate",
            aggregate_type="tenant",
            aggregate_id=str(tenant.id),
            payload={"reason": "courtesy_unlock_expired", "priorReason": prior_reason},
        )
        record_audit(
            db,
            action="billing.courtesy_unlock.expired",
            actor_email="system:billing_worker",
            target_type="tenant",
            target_id=str(tenant.id),
            before={"status": "active"},
            after={"status": tenant.status, "status_reason": tenant.status_reason},
        )
        expired += 1
    return expired


def run_dunning_pass(
    db: Session, *, grace_days: int = DEFAULT_GRACE_DAYS
) -> list[dict]:
    """Scan overdue open/past_due invoices and advance eligible subscriptions."""
    now = _now()
    results: list[dict] = []
    invoices = (
        db.execute(
            select(SaasInvoice).where(SaasInvoice.status.in_(("open", "past_due")))
        )
        .scalars()
        .all()
    )

    seen_subs: set[int] = set()
    for inv in invoices:
        if inv.due_at is None:
            continue
        due = (
            inv.due_at if inv.due_at.tzinfo else inv.due_at.replace(tzinfo=timezone.utc)
        )
        if due >= now:
            continue
        if not inv.subscription_id or inv.subscription_id in seen_subs:
            continue
        sub = db.get(Subscription, inv.subscription_id)
        if sub is None or not sub.is_current:
            continue
        seen_subs.add(sub.id)
        # Only auto-advance once per pass when overdue
        if sub.dunning_step in ("none", "past_due", "grace") or sub.status == "active":
            # If in grace and grace_until passed → suspend; else advance one step
            if sub.dunning_step == "grace" and sub.grace_until:
                gu = (
                    sub.grace_until
                    if sub.grace_until.tzinfo
                    else sub.grace_until.replace(tzinfo=timezone.utc)
                )
                if gu > now:
                    continue  # still in grace
            results.append(
                advance_dunning(
                    db, sub, grace_days=grace_days, staff_email="system:dunning"
                )
            )
    return results
