"""Resolve tutoring marketplace policy for runtime config and staff API."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tutoring import TutoringPolicy

DEFAULT_TUTORING_POLICY = {
    "platformFeePercent": 15.0,
    "defaultCurrency": "NGN",
    "allowExternalTutors": True,
    "marketplaceEnabled": True,
}


def get_tutoring_policy_row(db: Session, tenant_id: int) -> TutoringPolicy | None:
    return db.execute(
        select(TutoringPolicy).where(TutoringPolicy.tenant_id == tenant_id)
    ).scalar_one_or_none()


def tutoring_policy_payload(db: Session, tenant_id: int) -> dict:
    row = get_tutoring_policy_row(db, tenant_id)
    if row is None:
        return dict(DEFAULT_TUTORING_POLICY)
    return {
        "platformFeePercent": row.platform_fee_percent,
        "defaultCurrency": row.default_currency,
        "allowExternalTutors": row.allow_external_tutors,
        "marketplaceEnabled": row.marketplace_enabled,
    }
