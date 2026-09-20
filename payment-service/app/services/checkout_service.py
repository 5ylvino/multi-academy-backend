from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import PaymentCheckoutSession
from app.providers.base import CheckoutInput
from app.providers.registry import gateway_registry
from app.providers.stub import is_stub_gateway
from app.security.context import RequestContext
from app.services.control_client import control_client
from app.services.control_settings import control_payment_settings
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
        if self.settings.app_env == "production" and is_stub_gateway(gateway_id):
            raise GatewayResolutionError(f"Gateway '{gateway_id}' is not available in production")

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
            session.settled_at = datetime.now(UTC)
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
            "metadata": session.metadata_json,
        }

    async def list_contexts_for_tenant(
        self,
        db: Session,
        ctx: RequestContext,
    ) -> list[dict[str, Any]]:
        del db
        return await control_payment_settings.list_contexts_for_tenant(ctx.tenant_id, ctx.features)

    async def list_gateways_for_tenant(self, db: Session, tenant_id: str) -> list[dict[str, Any]]:
        del db
        return await control_payment_settings.list_gateways_for_tenant(tenant_id)


checkout_service = CheckoutService()
