from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

from app.providers.base import CheckoutInput, CheckoutResult, VerificationResult, WebhookParseResult


class PaystackAdapter:
    id = "paystack"
    label = "Paystack"

    def _secret(self, secrets: dict[str, str]) -> str:
        key = secrets.get("secret_key") or secrets.get("secretKey") or secrets.get("SECRET_KEY") or ""
        if not key:
            raise ValueError("Paystack secret_key missing from control vault")
        return key

    async def create_checkout(self, secrets: dict[str, str], mode: str, payload: CheckoutInput) -> CheckoutResult:
        secret = self._secret(secrets)
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(
                "https://api.paystack.co/transaction/initialize",
                headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
                json={
                    "email": payload.email,
                    "amount": payload.amount_minor,
                    "currency": payload.currency.upper(),
                    "reference": payload.reference,
                    "callback_url": payload.callback_url,
                    "metadata": payload.metadata,
                },
            )
        data = res.json()
        if not res.is_success or not data.get("status"):
            raise ValueError(data.get("message") or "Paystack initialize failed")
        body = data["data"]
        return CheckoutResult(
            provider_id=self.id,
            reference=payload.reference,
            checkout_url=body["authorization_url"],
            access_code=body.get("access_code"),
        )

    async def verify_transaction(self, secrets: dict[str, str], mode: str, reference: str) -> VerificationResult:
        secret = self._secret(secrets)
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.get(
                f"https://api.paystack.co/transaction/verify/{reference}",
                headers={"Authorization": f"Bearer {secret}"},
            )
        data = res.json()
        if not res.is_success or not data.get("status"):
            return VerificationResult(
                provider_id=self.id,
                reference=reference,
                status="failed",
                amount_minor=0,
                currency="NGN",
                raw=data,
            )
        tx: dict[str, Any] = data["data"]
        status = "success" if tx.get("status") == "success" else "failed" if tx.get("status") == "failed" else "pending"
        return VerificationResult(
            provider_id=self.id,
            reference=str(tx.get("reference") or reference),
            status=status,
            amount_minor=int(tx.get("amount") or 0),
            currency=str(tx.get("currency") or "NGN").upper(),
            provider_reference=str(tx.get("id") or tx.get("reference") or ""),
            raw=tx,
        )

    def verify_webhook_signature(self, secrets: dict[str, str], headers: dict[str, str], raw_body: bytes) -> bool:
        secret = self._secret(secrets)
        sig = headers.get("x-paystack-signature") or headers.get("X-Paystack-Signature") or ""
        digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
        return hmac.compare_digest(digest, sig)

    def parse_webhook(self, raw_body: bytes) -> WebhookParseResult:
        payload = json.loads(raw_body.decode("utf-8"))
        event = payload.get("event")
        data = payload.get("data") or {}
        reference = str(data.get("reference") or "")
        if not reference:
            return WebhookParseResult(reference="", status="ignored")
        if event == "charge.success" or data.get("status") == "success":
            return WebhookParseResult(
                reference=reference,
                status="success",
                amount_minor=int(data.get("amount") or 0),
                currency=str(data.get("currency") or "NGN").upper(),
            )
        if data.get("status") == "failed":
            return WebhookParseResult(reference=reference, status="failed")
        return WebhookParseResult(reference=reference, status="ignored")
