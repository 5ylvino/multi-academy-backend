"""Paystack adapter (https://paystack.com/docs/api).

- Amounts are sent and received in the currency's minor unit (kobo for NGN), as we store them.
- Webhooks are signed with HMAC-SHA512 of the raw body using the school's secret key, in
  ``x-paystack-signature``. A verified webhook only tells us which payment to check; the amount
  and status always come from ``GET /transaction/verify/{reference}``.
- Read calls retry once on network errors and 5xx. Initialising a payment never retries, so a
  slow provider can't open two payment pages.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx
import structlog

from app.domain.shared.errors import AuthenticationFailed, ProviderUnavailable, ValidationFailed
from app.use_cases.shared.ports import (
    CheckoutRequest,
    CheckoutSession,
    GatewayCredentials,
    GatewayNotice,
    GatewayTransaction,
)

_log = structlog.get_logger(__name__)

DEFAULT_BASE_URL = "https://api.paystack.co"
SUPPORTED_CURRENCIES = frozenset({"NGN", "GHS", "ZAR", "KES", "USD", "XOF", "EGP"})
_SECRET = re.compile(r"^sk_(live|test)_[A-Za-z0-9]{20,100}$")
_PUBLIC = re.compile(r"^pk_(live|test)_[A-Za-z0-9]{20,100}$")
#: Paystack statuses mapped onto ours. Anything else (ongoing, queued, processing) is pending.
_STATUS = {"success": "success", "failed": "failed", "abandoned": "abandoned", "reversed": "failed"}
_UNAVAILABLE = "The payment provider isn't responding. Please try again in a moment."


class PaystackGateway:
    provider = "paystack"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
            headers={"User-Agent": "masms-platform-api"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- keys ----------------------------------------------------------------------------

    def key_mode(self, secret_key: str) -> str:
        match = _SECRET.match(secret_key.strip())
        if match is None:
            raise ValidationFailed(
                "That doesn't look like a Paystack secret key (sk_live_... or sk_test_...).",
                details={"field": "secret_key"},
            )
        return match.group(1)

    def _check_public(self, public_key: str, mode: str) -> None:
        match = _PUBLIC.match(public_key.strip())
        if match is None:
            raise ValidationFailed(
                "That doesn't look like a Paystack public key (pk_live_... or pk_test_...).",
                details={"field": "public_key"},
            )
        if match.group(1) != mode:
            raise ValidationFailed(
                "The public and secret keys are from different modes (live and test).",
                details={"field": "public_key"},
            )

    async def check_credentials(self, credentials: GatewayCredentials) -> None:
        mode = self.key_mode(credentials.secret_key)
        self._check_public(credentials.public_key, mode)
        response = await self._send(
            "GET", "/transaction", credentials, params={"perPage": 1}, retry=True
        )
        if response.status_code == 401:
            raise ValidationFailed(
                "Paystack didn't accept this secret key. Copy it again from Settings > API Keys.",
                details={"field": "secret_key"},
            )
        self._ensure_ok(response)

    # ---- payments ------------------------------------------------------------------------

    async def initialize(
        self, credentials: GatewayCredentials, request: CheckoutRequest
    ) -> CheckoutSession:
        if request.currency not in SUPPORTED_CURRENCIES:
            raise ValidationFailed(
                f"Paystack can't take payments in {request.currency}.",
                details={"field": "currency"},
            )
        response = await self._send(
            "POST",
            "/transaction/initialize",
            credentials,
            body={
                "email": request.email,
                "amount": request.amount_minor,
                "currency": request.currency,
                "reference": request.reference,
                "callback_url": request.callback_url,
                "metadata": dict(request.metadata),
            },
            retry=False,
        )
        data = self._data(response)
        url = data.get("authorization_url")
        if not isinstance(url, str) or not url.startswith("https://"):
            _log.error("paystack.initialize_unexpected", reference=request.reference)
            raise ProviderUnavailable(_UNAVAILABLE)
        return CheckoutSession(authorization_url=url)

    async def verify(self, credentials: GatewayCredentials, reference: str) -> GatewayTransaction:
        response = await self._send(
            "GET",
            f"/transaction/verify/{quote(reference, safe='')}",
            credentials,
            retry=True,
        )
        if response.status_code in {400, 404}:
            # Paystack hasn't seen this reference: the payer never opened the payment page.
            return GatewayTransaction(
                reference=reference,
                status="pending",
                amount_minor=0,
                currency="",
                transaction_id=None,
                channel=None,
                gateway_response=None,
            )
        data = self._data(response)
        if data.get("reference") != reference:
            _log.error("paystack.verify_reference_mismatch", reference=reference)
            raise ProviderUnavailable(_UNAVAILABLE)
        raw_status = str(data.get("status") or "")
        amount = data.get("amount")
        return GatewayTransaction(
            reference=reference,
            status=_STATUS.get(raw_status, "pending"),
            amount_minor=int(amount)
            if isinstance(amount, int | str) and str(amount).isdigit()
            else 0,
            currency=str(data.get("currency") or "").upper(),
            transaction_id=str(data["id"]) if data.get("id") is not None else None,
            channel=str(data.get("channel") or "")[:32] or None,
            gateway_response=str(data.get("gateway_response") or "")[:200] or None,
        )

    def read_webhook(
        self, credentials: GatewayCredentials, headers: Mapping[str, str], body: bytes
    ) -> GatewayNotice:
        signature = headers.get("x-paystack-signature", "")
        expected = hmac.new(credentials.secret_key.encode(), body, hashlib.sha512).hexdigest()
        if not signature or not hmac.compare_digest(signature.lower(), expected):
            raise AuthenticationFailed("Webhook signature is not valid.", code="bad_signature")
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise ValidationFailed("Webhook body is not JSON.") from exc
        if not isinstance(payload, dict):
            raise ValidationFailed("Webhook body is not an object.")
        data = payload.get("data")
        reference = data.get("reference") if isinstance(data, dict) else None
        return GatewayNotice(
            event=str(payload.get("event") or ""),
            reference=str(reference)[:40] if reference else None,
        )

    # ---- HTTP ----------------------------------------------------------------------------

    async def _send(
        self,
        method: str,
        path: str,
        credentials: GatewayCredentials,
        *,
        retry: bool,
        params: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {credentials.secret_key}"}
        attempts = 2 if retry else 1
        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method, self._base + path, headers=headers, params=params, json=body
                )
            except httpx.HTTPError as exc:
                _log.warning("paystack.unreachable", path=path.split("/")[1], attempt=attempt)
                if attempt + 1 < attempts:
                    continue
                raise ProviderUnavailable(_UNAVAILABLE) from exc
            if response.status_code >= 500 and attempt + 1 < attempts:
                continue
            return response
        raise ProviderUnavailable(_UNAVAILABLE)  # pragma: no cover - loop always returns

    def _ensure_ok(self, response: httpx.Response) -> None:
        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderUnavailable(_UNAVAILABLE)
        if response.status_code >= 400:
            message = _message(response)
            raise ValidationFailed(f"Paystack refused the request: {message}")

    def _data(self, response: httpx.Response) -> dict[str, Any]:
        self._ensure_ok(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderUnavailable(_UNAVAILABLE) from exc
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict) or body.get("status") is not True:
            raise ProviderUnavailable(_UNAVAILABLE)
        return data


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    message = body.get("message") if isinstance(body, dict) else None
    return str(message)[:200] if message else f"HTTP {response.status_code}"
