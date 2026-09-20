from __future__ import annotations

import hmac
import json
from typing import Any

import httpx

from app.providers.base import CheckoutInput, CheckoutResult, VerificationResult, WebhookParseResult


class FlutterwaveAdapter:
    id = "flutterwave"
    label = "Flutterwave"

    def _secret(self, secrets: dict[str, str]) -> str:
        key = secrets.get("secret_key") or secrets.get("secretKey") or ""
        if not key:
            raise ValueError("Flutterwave secret_key missing from control vault")
        return key

    async def create_checkout(self, secrets: dict[str, str], mode: str, payload: CheckoutInput) -> CheckoutResult:
        secret = self._secret(secrets)
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(
                "https://api.flutterwave.com/v3/payments",
                headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
                json={
                    "tx_ref": payload.reference,
                    "amount": round(payload.amount_minor / 100, 2),
                    "currency": payload.currency.upper(),
                    "redirect_url": payload.callback_url,
                    "customer": {"email": payload.email},
                    "customizations": {"title": "School payment", "description": payload.reference},
                    "meta": payload.metadata,
                },
            )
        data = res.json()
        if not res.is_success:
            raise ValueError(data.get("message") or f"Flutterwave checkout failed ({res.status_code})")
        link = (data.get("data") or {}).get("link")
        if not link:
            raise ValueError("Flutterwave checkout link missing")
        return CheckoutResult(provider_id=self.id, reference=payload.reference, checkout_url=link)

    async def verify_transaction(self, secrets: dict[str, str], mode: str, reference: str) -> VerificationResult:
        secret = self._secret(secrets)
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.get(
                "https://api.flutterwave.com/v3/transactions/verify_by_reference",
                params={"tx_ref": reference},
                headers={"Authorization": f"Bearer {secret}"},
            )
        data = res.json()
        if not res.is_success:
            return VerificationResult(
                provider_id=self.id,
                reference=reference,
                status="failed",
                amount_minor=0,
                currency="NGN",
                raw=data,
            )
        tx: dict[str, Any] = (data.get("data") or {})
        flw_status = str(tx.get("status") or "").lower()
        status = "success" if flw_status == "successful" else "failed" if flw_status == "failed" else "pending"
        amount_minor = int(round(float(tx.get("amount") or 0) * 100))
        return VerificationResult(
            provider_id=self.id,
            reference=reference,
            status=status,
            amount_minor=amount_minor,
            currency=str(tx.get("currency") or "NGN").upper(),
            provider_reference=str(tx.get("id") or tx.get("flw_ref") or ""),
            raw=tx,
        )

    def verify_webhook_signature(self, secrets: dict[str, str], headers: dict[str, str], raw_body: bytes) -> bool:
        secret = (
            secrets.get("secret_hash")
            or secrets.get("secretHash")
            or secrets.get("webhook_secret")
            or secrets.get("secret_key")
            or ""
        )
        if not secret:
            return False
        signature = headers.get("verif-hash") or headers.get("Verif-Hash") or ""
        return bool(signature) and hmac.compare_digest(signature, secret)

    def parse_webhook(self, raw_body: bytes) -> WebhookParseResult:
        payload = json.loads(raw_body.decode("utf-8"))
        data = payload.get("data") or {}
        reference = str(data.get("tx_ref") or data.get("txRef") or "")
        if not reference:
            return WebhookParseResult(reference="", status="ignored")
        status_raw = str(data.get("status") or "").lower()
        if status_raw == "successful":
            amount_minor = int(round(float(data.get("amount") or 0) * 100))
            return WebhookParseResult(
                reference=reference,
                status="success",
                amount_minor=amount_minor,
                currency=str(data.get("currency") or "NGN").upper(),
            )
        if status_raw == "failed":
            return WebhookParseResult(reference=reference, status="failed")
        return WebhookParseResult(reference=reference, status="ignored")
