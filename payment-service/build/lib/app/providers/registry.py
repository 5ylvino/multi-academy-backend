from __future__ import annotations

from app.providers.base import PaymentGatewayAdapter
from app.providers.flutterwave import FlutterwaveAdapter
from app.providers.paystack import PaystackAdapter
from app.providers.stub import StubMobileMoneyAdapter, build_stub_adapters


class GatewayRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, PaymentGatewayAdapter] = {}
        self.register(PaystackAdapter())
        self.register(FlutterwaveAdapter())
        for adapter in build_stub_adapters().values():
            self.register(adapter)

    def register(self, adapter: PaymentGatewayAdapter) -> None:
        self._adapters[adapter.id] = adapter

    def get(self, gateway_id: str) -> PaymentGatewayAdapter | None:
        if gateway_id == "disabled":
            return None
        return self._adapters.get(gateway_id)

    def list_known(self) -> list[PaymentGatewayAdapter]:
        return list(self._adapters.values())


gateway_registry = GatewayRegistry()
