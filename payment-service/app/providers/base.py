from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CheckoutInput:
    amount_minor: int
    currency: str
    reference: str
    email: str
    callback_url: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class CheckoutResult:
    provider_id: str
    reference: str
    checkout_url: str
    access_code: str | None = None


@dataclass(frozen=True)
class VerificationResult:
    provider_id: str
    reference: str
    status: str  # success | failed | pending
    amount_minor: int
    currency: str
    provider_reference: str = ""
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class WebhookParseResult:
    reference: str
    status: str  # success | failed | ignored
    amount_minor: int = 0
    currency: str = "NGN"


class PaymentGatewayAdapter(Protocol):
    id: str
    label: str

    async def create_checkout(self, secrets: dict[str, str], mode: str, payload: CheckoutInput) -> CheckoutResult: ...

    async def verify_transaction(
        self, secrets: dict[str, str], mode: str, reference: str
    ) -> VerificationResult: ...

    def verify_webhook_signature(
        self, secrets: dict[str, str], headers: dict[str, str], raw_body: bytes
    ) -> bool: ...

    def parse_webhook(self, raw_body: bytes) -> WebhookParseResult: ...
