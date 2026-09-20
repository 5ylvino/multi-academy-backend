"""Staff API — tenant payment button and gateway settings (control console only)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audit import bump_config_version, current_config_version, record_audit
from app.database import get_db
from app.deps import StaffContext, require_permissions
from app.models import Tenant
from app.models.payment_settings import TenantPaymentContextBinding, TenantPaymentGatewayToggle
from app.schemas import (
    PaymentContextItemOut,
    PaymentContextListOut,
    PaymentContextUpdate,
    PaymentGatewayItemOut,
    PaymentGatewayListOut,
    PaymentGatewayUpdate,
)
from app.payment_contexts import DEFAULT_PAYMENT_GATEWAY
from app.services.payment_settings import (
    get_context_binding,
    get_gateway_toggle,
    list_payment_contexts_for_tenant,
    list_payment_gateways_for_tenant,
    resolve_context_gateway_id,
    validate_context_gateway,
    validate_gateway_id,
)
from app.services.runtime_config import _get_catalog_snapshot, resolve_features

router = APIRouter(prefix="/v1/tenants", tags=["payment-settings"])


def _tenant_or_404(db: Session, tenant_id: int) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    return tenant


def _tenant_features(db: Session, tenant: Tenant) -> dict[str, bool]:
    catalog = _get_catalog_snapshot(db, current_config_version(db))
    features, _, _ = resolve_features(db, tenant, catalog)
    return features


@router.get("/{tenant_id}/payment-contexts", response_model=PaymentContextListOut)
def list_payment_contexts(
    tenant_id: int,
    _: StaffContext = Depends(require_permissions("billing:read")),
    db: Session = Depends(get_db),
):
    tenant = _tenant_or_404(db, tenant_id)
    items = list_payment_contexts_for_tenant(db, tenant, _tenant_features(db, tenant))
    return PaymentContextListOut(items=[PaymentContextItemOut(**item) for item in items])


@router.patch("/{tenant_id}/payment-contexts/{context_key}", response_model=PaymentContextItemOut)
def update_payment_context(
    tenant_id: int,
    context_key: str,
    body: PaymentContextUpdate,
    staff: StaffContext = Depends(require_permissions("billing:write")),
    db: Session = Depends(get_db),
):
    tenant = _tenant_or_404(db, tenant_id)
    if body.clearGateway:
        gateway_id = DEFAULT_PAYMENT_GATEWAY
    elif body.gatewayId is not None:
        gateway_id = body.gatewayId.strip() or DEFAULT_PAYMENT_GATEWAY
    else:
        existing = get_context_binding(db, tenant_id, context_key)
        gateway_id = resolve_context_gateway_id(existing.gateway_id if existing else None)

    validate_context_gateway(context_key, gateway_id)

    binding = get_context_binding(db, tenant_id, context_key)
    if binding is None:
        binding = TenantPaymentContextBinding(
            tenant_id=tenant_id,
            context_key=context_key,
            enabled=True if body.enabled is None else body.enabled,
            gateway_id=gateway_id,
        )
        db.add(binding)
    else:
        if body.enabled is not None:
            binding.enabled = body.enabled
        if body.clearGateway or body.gatewayId is not None:
            binding.gateway_id = gateway_id
    binding.updated_by = staff.email

    record_audit(
        db,
        action="payment.context.update",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="tenant_payment_context",
        target_id=f"{tenant_id}:{context_key}",
        after={"enabled": binding.enabled, "gateway_id": binding.gateway_id},
    )
    bump_config_version(db)
    db.commit()

    features = _tenant_features(db, tenant)
    items = list_payment_contexts_for_tenant(db, tenant, features)
    match = next((item for item in items if item["contextKey"] == context_key), None)
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment context not found")
    return PaymentContextItemOut(**match)


@router.get("/{tenant_id}/payment-gateways", response_model=PaymentGatewayListOut)
def list_payment_gateways(
    tenant_id: int,
    _: StaffContext = Depends(require_permissions("billing:read")),
    db: Session = Depends(get_db),
):
    _tenant_or_404(db, tenant_id)
    items = list_payment_gateways_for_tenant(db, tenant_id)
    return PaymentGatewayListOut(items=[PaymentGatewayItemOut(**item) for item in items])


@router.patch("/{tenant_id}/payment-gateways/{gateway_id}", response_model=PaymentGatewayItemOut)
def update_payment_gateway(
    tenant_id: int,
    gateway_id: str,
    body: PaymentGatewayUpdate,
    staff: StaffContext = Depends(require_permissions("billing:write")),
    db: Session = Depends(get_db),
):
    _tenant_or_404(db, tenant_id)
    validate_gateway_id(gateway_id)

    toggle = get_gateway_toggle(db, tenant_id, gateway_id)
    if toggle is None:
        toggle = TenantPaymentGatewayToggle(
            tenant_id=tenant_id,
            gateway_id=gateway_id,
            enabled=body.enabled,
        )
        db.add(toggle)
    else:
        toggle.enabled = body.enabled
    toggle.updated_by = staff.email

    record_audit(
        db,
        action="payment.gateway.update",
        actor_id=staff.id,
        actor_email=staff.email,
        target_type="tenant_payment_gateway",
        target_id=f"{tenant_id}:{gateway_id}",
        after={"enabled": toggle.enabled},
    )
    bump_config_version(db)
    db.commit()

    items = list_payment_gateways_for_tenant(db, tenant_id)
    match = next((item for item in items if item["gatewayId"] == gateway_id), None)
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gateway not found")
    return PaymentGatewayItemOut(**match)
