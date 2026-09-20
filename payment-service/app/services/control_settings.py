"""Read tenant payment settings from control-plane runtime config."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.control_client import ControlClient, control_client
from app.services.context_registry import PAYMENT_CONTEXTS, list_contexts

DEFAULT_PAYMENT_GATEWAY = "paystack"


@dataclass(frozen=True)
class ContextBindingView:
    enabled: bool
    gateway_id: str | None


class ControlPaymentSettingsStore:
    def __init__(self, control: ControlClient | None = None) -> None:
        self.control = control or control_client

    async def _settings(self, tenant_id: str) -> dict:
        if not self.control.is_configured():
            return {}
        getter = getattr(self.control, "get_runtime_config_safe", self.control.get_runtime_config)
        config = await getter(tenant_id)
        return config.get("paymentSettings") or {}

    async def context_binding(self, tenant_id: str, context_key: str) -> ContextBindingView | None:
        settings = await self._settings(tenant_id)
        contexts = settings.get("contexts") or []
        match = next((row for row in contexts if row.get("contextKey") == context_key), None)
        if match is None:
            ctx_def = PAYMENT_CONTEXTS.get(context_key)
            if ctx_def is None:
                return None
            return ContextBindingView(
                enabled=ctx_def.default_enabled,
                gateway_id=DEFAULT_PAYMENT_GATEWAY,
            )
        stored_gateway = match.get("gatewayId")
        return ContextBindingView(
            enabled=bool(match.get("buttonEnabled", match.get("enabled", True))),
            gateway_id=stored_gateway or DEFAULT_PAYMENT_GATEWAY,
        )

    async def gateway_enabled(self, tenant_id: str, gateway_id: str) -> bool:
        settings = await self._settings(tenant_id)
        gateways = settings.get("gateways") or []
        match = next((row for row in gateways if row.get("gatewayId") == gateway_id), None)
        if match is None:
            return True
        return bool(match.get("enabled", True))

    async def list_contexts_for_tenant(
        self,
        tenant_id: str,
        features: set[str] | list[str],
    ) -> list[dict]:
        feature_set = set(features)
        settings = await self._settings(tenant_id)
        if settings.get("contexts"):
            return list(settings["contexts"])

        # Dev fallback when control is not configured.
        items: list[dict] = []
        for ctx_def in list_contexts():
            features_ok = all(f in feature_set for f in ctx_def.required_features)
            items.append(
                {
                    "contextKey": ctx_def.key,
                    "label": ctx_def.label,
                    "description": ctx_def.description,
                    "enabled": ctx_def.default_enabled and features_ok,
                    "buttonEnabled": ctx_def.default_enabled,
                    "featuresSatisfied": features_ok,
                    "requiredFeatures": list(ctx_def.required_features),
                    "gatewayId": DEFAULT_PAYMENT_GATEWAY,
                    "allowedGateways": list(ctx_def.allowed_gateways),
                }
            )
        return items

    async def list_gateways_for_tenant(self, tenant_id: str) -> list[dict]:
        settings = await self._settings(tenant_id)
        if settings.get("gateways"):
            return list(settings["gateways"])

        from app.providers.registry import gateway_registry

        return [
            {"gatewayId": adapter.id, "label": adapter.label, "enabled": True}
            for adapter in gateway_registry.list_known()
        ]


control_payment_settings = ControlPaymentSettingsStore()
