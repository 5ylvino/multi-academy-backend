"""Resolve tenant payment button and gateway settings for runtime config and staff API."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Tenant
from app.models.payment_settings import TenantPaymentContextBinding, TenantPaymentGatewayToggle
from app.payment_contexts import (
    DEFAULT_PAYMENT_GATEWAY,
    GATEWAY_LABELS,
    KNOWN_PAYMENT_GATEWAYS,
    get_payment_context,
    list_payment_contexts,
)


def resolve_context_gateway_id(stored: str | None) -> str:
    return stored or DEFAULT_PAYMENT_GATEWAY


def _context_bindings_map(db: Session, tenant_id: int) -> dict[str, TenantPaymentContextBinding]:
    rows = db.execute(
        select(TenantPaymentContextBinding).where(TenantPaymentContextBinding.tenant_id == tenant_id)
    ).scalars().all()
    return {row.context_key: row for row in rows}


def _gateway_toggles_map(db: Session, tenant_id: int) -> dict[str, TenantPaymentGatewayToggle]:
    rows = db.execute(
        select(TenantPaymentGatewayToggle).where(TenantPaymentGatewayToggle.tenant_id == tenant_id)
    ).scalars().all()
    return {row.gateway_id: row for row in rows}


def list_payment_contexts_for_tenant(
    db: Session,
    tenant: Tenant,
    features: dict[str, bool],
) -> list[dict]:
    bindings = _context_bindings_map(db, tenant.id)
    items: list[dict] = []
    for ctx_def in list_payment_contexts():
        binding = bindings.get(ctx_def.key)
        button_enabled = binding.enabled if binding is not None else ctx_def.default_enabled
        features_ok = all(features.get(f, False) for f in ctx_def.required_features)
        items.append(
            {
                "contextKey": ctx_def.key,
                "label": ctx_def.label,
                "description": ctx_def.description,
                "enabled": button_enabled and features_ok,
                "buttonEnabled": button_enabled,
                "featuresSatisfied": features_ok,
                "requiredFeatures": list(ctx_def.required_features),
                "gatewayId": resolve_context_gateway_id(binding.gateway_id if binding else None),
                "allowedGateways": list(ctx_def.allowed_gateways),
            }
        )
    return items


def list_payment_gateways_for_tenant(db: Session, tenant_id: int) -> list[dict]:
    toggles = _gateway_toggles_map(db, tenant_id)
    return [
        {
            "gatewayId": gateway_id,
            "label": GATEWAY_LABELS.get(gateway_id, gateway_id),
            "enabled": toggles[gateway_id].enabled
            if gateway_id in toggles
            else gateway_id == DEFAULT_PAYMENT_GATEWAY,
        }
        for gateway_id in KNOWN_PAYMENT_GATEWAYS
    ]


def payment_settings_payload(
    db: Session,
    tenant: Tenant,
    features: dict[str, bool],
) -> dict:
    return {
        "contexts": list_payment_contexts_for_tenant(db, tenant, features),
        "gateways": list_payment_gateways_for_tenant(db, tenant.id),
    }


def get_context_binding(
    db: Session,
    tenant_id: int,
    context_key: str,
) -> TenantPaymentContextBinding | None:
    return db.execute(
        select(TenantPaymentContextBinding).where(
            TenantPaymentContextBinding.tenant_id == tenant_id,
            TenantPaymentContextBinding.context_key == context_key,
        )
    ).scalar_one_or_none()


def get_gateway_toggle(
    db: Session,
    tenant_id: int,
    gateway_id: str,
) -> TenantPaymentGatewayToggle | None:
    return db.execute(
        select(TenantPaymentGatewayToggle).where(
            TenantPaymentGatewayToggle.tenant_id == tenant_id,
            TenantPaymentGatewayToggle.gateway_id == gateway_id,
        )
    ).scalar_one_or_none()


def validate_context_gateway(context_key: str, gateway_id: str | None) -> None:
    ctx_def = get_payment_context(context_key)
    if ctx_def is None:
        raise ValueError(f"Unknown payment context: {context_key}")
    if gateway_id and gateway_id not in ctx_def.allowed_gateways:
        raise ValueError(f"Gateway '{gateway_id}' is not allowed for context '{context_key}'")


def validate_gateway_id(gateway_id: str) -> None:
    if gateway_id not in KNOWN_PAYMENT_GATEWAYS:
        raise ValueError(f"Unknown gateway: {gateway_id}")
