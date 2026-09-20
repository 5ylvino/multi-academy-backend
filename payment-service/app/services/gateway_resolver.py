from __future__ import annotations

from sqlalchemy.orm import Session

from app.providers.registry import gateway_registry
from app.services.context_registry import PaymentContextDef, get_context
from app.services.control_client import ControlClient, control_client
from app.services.control_settings import ControlPaymentSettingsStore, control_payment_settings


class GatewayResolutionError(Exception):
    pass


class GatewayResolver:
    def __init__(
        self,
        control: ControlClient | None = None,
        settings_store: ControlPaymentSettingsStore | None = None,
    ) -> None:
        self.control = control or control_client
        self.settings = settings_store or control_payment_settings

    async def resolve(
        self,
        db: Session,
        *,
        tenant_id: str,
        context_key: str,
        requested_gateway: str | None = None,
    ) -> tuple[str, PaymentContextDef]:
        del db  # settings are control-plane owned; local DB bindings are deprecated.
        ctx_def = get_context(context_key)
        if ctx_def is None:
            raise GatewayResolutionError(f"Unknown payment context: {context_key}")

        binding = await self.settings.context_binding(tenant_id, context_key)
        if binding is not None and not binding.enabled:
            raise GatewayResolutionError(f"Payment button '{context_key}' is disabled for this school")

        gateway_id = requested_gateway or (binding.gateway_id if binding and binding.gateway_id else None)
        if not gateway_id:
            secret_scope = "__platform__" if context_key == "saas_subscription" else tenant_id
            gateway_id = await self.control.default_gateway_id(secret_scope)

        if gateway_id == "disabled":
            raise GatewayResolutionError("Payment gateway is disabled in control plane")

        if gateway_id not in ctx_def.allowed_gateways:
            raise GatewayResolutionError(
                f"Gateway '{gateway_id}' is not allowed for payment context '{context_key}'"
            )

        if gateway_registry.get(gateway_id) is None:
            raise GatewayResolutionError(f"Gateway adapter '{gateway_id}' is not registered")

        if not await self.settings.gateway_enabled(tenant_id, gateway_id):
            raise GatewayResolutionError(f"Gateway '{gateway_id}' is disabled for this school")

        return gateway_id, ctx_def

    async def secret_scope(self, tenant_id: str, context_key: str) -> str:
        return "__platform__" if context_key == "saas_subscription" else tenant_id
