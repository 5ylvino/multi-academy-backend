from __future__ import annotations

import json
import secrets

from app.providers.base import CheckoutInput, CheckoutResult, VerificationResult, WebhookParseResult


class StubMobileMoneyAdapter:
    """Development stub for OPay / PalmPay / MoMo until live APIs are wired."""

    def __init__(self, gateway_id: str, label: str) -> None:
        self.id = gateway_id
        self.label = label

    async def create_checkout(self, secrets: dict[str, str], mode: str, payload: CheckoutInput) -> CheckoutResult:
        token = secrets.get("merchant_id") or secrets.get("public_key") or "demo"
        url = f"https://checkout.example/{self.id}?ref={payload.reference}&token={token[:8]}"
        return CheckoutResult(provider_id=self.id, reference=payload.reference, checkout_url=url)

    async def verify_transaction(self, secrets: dict[str, str], mode: str, reference: str) -> VerificationResult:
        if reference.startswith("demo_paid_"):
            return VerificationResult(
                provider_id=self.id,
                reference=reference,
                status="success",
                amount_minor=0,
                currency="NGN",
                provider_reference=f"{self.id}-{secrets.get('merchant_id', 'demo')}",
            )
        return VerificationResult(
            provider_id=self.id,
            reference=reference,
            status="pending",
            amount_minor=0,
            currency="NGN",
        )

    def verify_webhook_signature(self, secrets: dict[str, str], headers: dict[str, str], raw_body: bytes) -> bool:
        expected = secrets.get("webhook_secret") or secrets.get("secret_key") or ""
        if not expected:
            return False
        return headers.get("x-webhook-secret") == expected

    def parse_webhook(self, raw_body: bytes) -> WebhookParseResult:
        payload = json.loads(raw_body.decode("utf-8"))
        reference = str(payload.get("reference") or "")
        status = str(payload.get("status") or "ignored")
        return WebhookParseResult(reference=reference, status=status)


def build_stub_adapters() -> dict[str, StubMobileMoneyAdapter]:
    return {
        "opay": StubMobileMoneyAdapter("opay", "OPay"),
        "palmpay": StubMobileMoneyAdapter("palmpay", "PalmPay"),
        "momo": StubMobileMoneyAdapter("momo", "MoMo"),
        "monnify": StubMobileMoneyAdapter("monnify", "Monnify"),
    }


def demo_reference(prefix: str = "demo_paid") -> str:
    return f"{prefix}_{secrets.token_hex(8)}"
