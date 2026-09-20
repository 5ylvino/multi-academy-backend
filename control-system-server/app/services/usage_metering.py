"""Persist usage events + rollups; expose period totals vs quotas."""

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AbuseSignal, Subscription, Tenant, UsageEvent, UsageMeter, UsageRollup


DEFAULT_METERS = [
    ("sms_segments", "SMS segments", "segment", "comms.sms"),
    ("email_sends", "Email sends", "count", "comms.email"),
    ("gateway_checkouts", "Payment checkouts", "count", "fees.gateway"),
    ("ai_tokens", "AI tokens", "token", "ai.assistant"),
]


def ensure_default_meters(db: Session) -> None:
    existing = {m.key for m in db.execute(select(UsageMeter)).scalars().all()}
    for key, name, unit, feature_key in DEFAULT_METERS:
        if key not in existing:
            db.add(
                UsageMeter(
                    key=key,
                    name=name,
                    unit=unit,
                    feature_key=feature_key,
                    description=f"Auto-seeded meter for {feature_key or key}",
                )
            )


def period_key(when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    return when.strftime("%Y-%m")


def ingest_usage_event(
    db: Session,
    *,
    tenant_ref: str | None,
    meter_key: str,
    quantity: int = 1,
    event_id: str = "",
    properties: dict | None = None,
    occurred_at: datetime | None = None,
) -> UsageEvent:
    tenant_id = None
    if tenant_ref:
        tenant = db.execute(
            select(Tenant).where(
                (Tenant.external_id == tenant_ref) | (Tenant.slug == tenant_ref)
            )
        ).scalar_one_or_none()
        if tenant is not None:
            tenant_id = tenant.id

    if event_id:
        existing = db.execute(
            select(UsageEvent).where(UsageEvent.event_id == event_id)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    when = occurred_at or datetime.now(timezone.utc)
    event = UsageEvent(
        tenant_id=tenant_id,
        meter_key=meter_key,
        quantity=max(0, int(quantity)),
        event_id=event_id or "",
        properties=json.dumps(properties or {}),
        occurred_at=when,
    )
    db.add(event)

    if tenant_id is not None:
        pk = period_key(when)
        rollup = db.execute(
            select(UsageRollup).where(
                UsageRollup.tenant_id == tenant_id,
                UsageRollup.meter_key == meter_key,
                UsageRollup.period_key == pk,
            )
        ).scalar_one_or_none()
        if rollup is None:
            rollup = UsageRollup(
                tenant_id=tenant_id,
                meter_key=meter_key,
                period_key=pk,
                quantity=0,
            )
            db.add(rollup)
            db.flush()
        rollup.quantity += event.quantity
        _check_usage_caps(db, tenant_id=tenant_id, meter_key=meter_key, quantity=rollup.quantity)

    return event


def _resolve_quota(db: Session, tenant_id: int, meter_key: str) -> int | None:
    """Resolve quota for meter from current subscription."""
    meter = db.execute(select(UsageMeter).where(UsageMeter.key == meter_key)).scalar_one_or_none()
    if meter is None or not meter.feature_key:
        return None
    sub = db.execute(
        select(Subscription)
        .where(Subscription.tenant_id == tenant_id, Subscription.is_current.is_(True))
        .order_by(Subscription.id.desc())
    ).scalars().first()
    if sub is None:
        return None
    quotas = sub.custom_quotas or {}
    if meter.feature_key in quotas:
        return int(quotas[meter.feature_key])
    # Also check meter_key directly in quotas
    if meter_key in quotas:
        return int(quotas[meter_key])
    return None


def _check_usage_caps(db: Session, *, tenant_id: int, meter_key: str, quantity: int) -> None:
    """Create AbuseSignal when soft (80%) or hard (100%) quota cap is crossed."""
    quota = _resolve_quota(db, tenant_id, meter_key)
    if quota is None or quota <= 0:
        return
    soft_threshold = int(quota * 0.8)
    signal_type = None
    score = 0
    if quantity >= quota:
        signal_type = "usage_hard_cap"
        score = 10
    elif quantity >= soft_threshold:
        signal_type = "usage_soft_cap"
        score = 5
    if signal_type is None:
        return

    # Dedupe: one signal per tenant/meter/type per period
    pk = period_key()
    existing = db.execute(
        select(AbuseSignal).where(
            AbuseSignal.tenant_id == tenant_id,
            AbuseSignal.signal_type == signal_type,
            AbuseSignal.value == f"{meter_key}:{pk}",
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.score = max(existing.score, score)
        existing.evidence = f"quantity={quantity}, quota={quota}, period={pk}"
        return

    db.add(
        AbuseSignal(
            tenant_id=tenant_id,
            signal_type=signal_type,
            value=f"{meter_key}:{pk}",
            score=score,
            evidence=f"quantity={quantity}, quota={quota}, period={pk}",
            auto_blocked=False,
        )
    )


def usage_alerts_summary(db: Session, *, period: str | None = None) -> list[dict]:
    pk = period or period_key()
    signals = db.execute(
        select(AbuseSignal).where(AbuseSignal.signal_type.in_(("usage_soft_cap", "usage_hard_cap")))
    ).scalars().all()
    tenants = {t.id: t for t in db.execute(select(Tenant)).scalars().all()}
    out = []
    for s in signals:
        if not s.value.endswith(f":{pk}"):
            continue
        meter_key = s.value.rsplit(":", 1)[0]
        out.append(
            {
                "tenantId": s.tenant_id,
                "tenantSlug": tenants[s.tenant_id].slug if s.tenant_id in tenants else None,
                "meterKey": meter_key,
                "signalType": s.signal_type,
                "score": s.score,
                "evidence": s.evidence,
                "createdAt": s.created_at.isoformat() if s.created_at else None,
            }
        )
    return out


def usage_csv_rows(db: Session, *, period: str | None = None) -> list[str]:
    rows = usage_summary(db, period=period)
    lines = ["tenant_id,tenant_slug,meter_key,period_key,quantity"]
    for r in rows:
        lines.append(
            f"{r['tenantId']},{r.get('tenantSlug') or ''},{r['meterKey']},{r['periodKey']},{r['quantity']}"
        )
    return lines


def usage_summary(db: Session, *, period: str | None = None) -> list[dict]:
    pk = period or period_key()
    rows = db.execute(select(UsageRollup).where(UsageRollup.period_key == pk)).scalars().all()
    tenants = {
        t.id: t
        for t in db.execute(select(Tenant)).scalars().all()
    }
    return [
        {
            "tenantId": r.tenant_id,
            "tenantSlug": tenants[r.tenant_id].slug if r.tenant_id in tenants else None,
            "meterKey": r.meter_key,
            "periodKey": r.period_key,
            "quantity": r.quantity,
        }
        for r in rows
    ]
