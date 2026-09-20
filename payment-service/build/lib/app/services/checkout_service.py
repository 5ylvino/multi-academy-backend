from __future__ import annotations

import secrets
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import PaymentCheckoutSession, PaymentContextBinding, PaymentGatewayOverride
from app.providers.base import CheckoutInput
from app.providers.registry import gateway_registry
from app.security.context import RequestContext
from app.services.context_registry import PAYMENT_CONTEXTS, get_context, list_contexts
from app.services.control_client import control_client
from app.services.gateway_resolver import GatewayResolutionError, GatewayResolver


class CheckoutService:
    def __init__(self) -> None:
        self.resolver = GatewayResolver()
        self.settings = get_settings()

    def _assert_features(self, ctx: RequestContext, required: tuple[str, ...]) -> None:
        missing = [f for f in required if f not in ctx.features]
        if missing:
            raise ValueError(f"Required features not enabled: {', '.join(missing)}")

    def _reference(self, tenant_id: str) -> str:
        return f"pay_{tenant_id[:8]}_{secrets.token_hex(8)}"

    async def create_checkout(
        self,
        db: Session,
        ctx: RequestContext,
        *,
        context_key: str,
        amount_minor: int,
        email: str,
        callback_url: str,
        currency: str | None = None,
        reference: str | None = None,
        metadata: dict[str, Any] | None = None,
        gateway_id: str | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        if amount_minor <= 0:
            raise ValueError("Invalid amount")

        gateway_id, ctx_def = await self.resolver.resolve(
            db,
            tenant_id=ctx.tenant_id,
            context_key=context_key,
            requested_gateway=gateway_id,
        )
        self._assert_features(ctx, ctx_def.required_features)

        adapter = gateway_registry.get(gateway_id)
        if adapter is None:
            raise GatewayResolutionError(f"Gateway '{gateway_id}' unavailable")

        secret_scope = await self.resolver.secret_scope(ctx.tenant_id, context_key)
        vault = await control_client.get_provider_secrets(secret_scope, "payment")
        if vault.get("providerId") and vault["providerId"] != gateway_id:
            # Allow context override even when control default differs; still fetch tenant secrets.
            pass

        ref = reference or self._reference(ctx.tenant_id)
        meta = dict(metadata or {})
        meta.update(
            {
                "tenantId": ctx.tenant_id,
                "contextKey": context_key,
                "gatewayId": gateway_id,
            }
        )

        session = PaymentCheckoutSession(
            id=uuid.uuid4(),
            tenant_id=ctx.tenant_id,
            context_key=context_key,
            gateway_id=gateway_id,
            reference=ref,
            amount_minor=amount_minor,
            currency=(currency or self.settings.default_currency).upper(),
            status="pending",
            email=email,
            callback_url=callback_url,
            metadata_json=meta,
            created_by=created_by or ctx.actor_id,
        )
        db.add(session)
        db.flush()

        checkout = await adapter.create_checkout(
            vault.get("secrets") or {},
            str(vault.get("mode") or "live"),
            CheckoutInput(
                amount_minor=amount_minor,
                currency=session.currency,
                reference=ref,
                email=email,
                callback_url=callback_url,
                metadata=meta,
            ),
        )
        db.commit()
        db.refresh(session)

        return {
            "sessionId": str(session.id),
            "reference": ref,
            "contextKey": context_key,
            "gatewayId": gateway_id,
            "checkoutUrl": checkout.checkout_url,
            "accessCode": checkout.access_code,
            "status": session.status,
        }

    async def verify(
        self,
        db: Session,
        ctx: RequestContext,
        *,
        reference: str,
    ) -> dict[str, Any]:
        session = (
            db.query(PaymentCheckoutSession)
            .filter(
                PaymentCheckoutSession.tenant_id == ctx.tenant_id,
                PaymentCheckoutSession.reference == reference,
            )
            .one_or_none()
        )
        if session is None:
            raise ValueError("Checkout session not found")

        adapter = gateway_registry.get(session.gateway_id)
        if adapter is None:
            raise GatewayResolutionError(f"Gateway '{session.gateway_id}' unavailable")

        secret_scope = await self.resolver.secret_scope(ctx.tenant_id, session.context_key)
        vault = await control_client.get_provider_secrets(secret_scope, "payment")
        result = await adapter.verify_transaction(
            vault.get("secrets") or {},
            str(vault.get("mode") or "live"),
            reference,
        )

        if result.status == "success" and session.status != "settled":
            session.status = "settled"
            session.provider_reference = result.provider_reference or reference
            db.commit()

        return {
            "reference": result.reference,
            "status": result.status,
            "gatewayId": result.provider_id,
            "amountMinor": result.amount_minor,
            "currency": result.currency,
            "contextKey": session.context_key,
            "sessionId": str(session.id),
            "providerReference": result.provider_reference,
        }

    def list_contexts_for_tenant(
        self,
        db: Session,
        ctx: RequestContext,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for ctx_def in list_contexts():
            binding = (
                db.query(PaymentContextBinding)
                .filter(
                    PaymentContextBinding.tenant_id == ctx.tenant_id,
                    PaymentContextBinding.context_key == ctx_def.key,
                )
                .one_or_none()
            )
            enabled = binding.enabled if binding is not None else ctx_def.default_enabled
            features_ok = all(f in ctx.features for f in ctx_def.required_features)
            items.append(
                {
                    "contextKey": ctx_def.key,
                    "label": ctx_def.label,
                    "description": ctx_def.description,
                    "enabled": enabled and features_ok,
                    "buttonEnabled": enabled,
                    "featuresSatisfied": features_ok,
                    "requiredFeatures": list(ctx_def.required_features),
                    "gatewayId": binding.gateway_id if binding else None,
                    "allowedGateways": list(ctx_def.allowed_gateways),
                }
            )
        return items

    def update_context_binding(
        self,
        db: Session,
        tenant_id: str,
        context_key: str,
        *,
        enabled: bool | None = None,
        gateway_id: str | None = None,
        clear_gateway: bool = False,
    ) -> dict[str, Any]:
        if get_context(context_key) is None:
            raise ValueError(f"Unknown payment context: {context_key}")

        binding = (
            db.query(PaymentContextBinding)
            .filter(
                PaymentContextBinding.tenant_id == tenant_id,
                PaymentContextBinding.context_key == context_key,
            )
            .one_or_none()
        )
        if binding is None:
            binding = PaymentContextBinding(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                context_key=context_key,
                enabled=True if enabled is None else enabled,
            )
            db.add(binding)
        if enabled is not None:
            binding.enabled = enabled
        if clear_gateway:
            binding.gateway_id = None
        elif gateway_id is not None:
            ctx_def = get_context(context_key)
            if ctx_def and gateway_id not in ctx_def.allowed_gateways:
                raise ValueError(f"Gateway '{gateway_id}' not allowed for context '{context_key}'")
            binding.gateway_id = gateway_id
        db.commit()
        db.refresh(binding)
        return {
            "contextKey": binding.context_key,
            "enabled": binding.enabled,
            "gatewayId": binding.gateway_id,
        }

    def list_gateways_for_tenant(self, db: Session, tenant_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for adapter in gateway_registry.list_known():
            override = (
                db.query(PaymentGatewayOverride)
                .filter(
                    PaymentGatewayOverride.tenant_id == tenant_id,
                    PaymentGatewayOverride.gateway_id == adapter.id,
                )
                .one_or_none()
            )
            rows.append(
                {
                    "gatewayId": adapter.id,
                    "label": adapter.label,
                    "enabled": override.enabled if override is not None else True,
                }
            )
        return rows

    def update_gateway_override(
        self,
        db: Session,
        tenant_id: str,
        gateway_id: str,
        *,
        enabled: bool,
    ) -> dict[str, Any]:
        if gateway_registry.get(gateway_id) is None:
            raise ValueError(f"Unknown gateway: {gateway_id}")
        override = (
            db.query(PaymentGatewayOverride)
            .filter(
                PaymentGatewayOverride.tenant_id == tenant_id,
                PaymentGatewayOverride.gateway_id == gateway_id,
            )
            .one_or_none()
        )
        if override is None:
            override = PaymentGatewayOverride(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                gateway_id=gateway_id,
                enabled=enabled,
            )
            db.add(override)
        else:
            override.enabled = enabled
        db.commit()
        return {"gatewayId": gateway_id, "enabled": enabled}


checkout_service = CheckoutService()
