"""The payment providers this deployment can use."""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.shared.errors import ValidationFailed
from app.use_cases.shared.ports import PaymentGateway


class GatewayRegistry:
    def __init__(self, gateways: Iterable[PaymentGateway]) -> None:
        self._gateways = {g.provider: g for g in gateways}

    def get(self, provider: str) -> PaymentGateway:
        gateway = self._gateways.get(provider)
        if gateway is None:
            raise ValidationFailed(
                "That payment provider isn't available.", details={"field": "provider"}
            )
        return gateway

    def providers(self) -> list[str]:
        return sorted(self._gateways)

    async def aclose(self) -> None:
        for gateway in self._gateways.values():
            close = getattr(gateway, "aclose", None)
            if close is not None:
                await close()
