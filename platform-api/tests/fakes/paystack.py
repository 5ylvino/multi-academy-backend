"""An in-memory Paystack API good enough to drive the adapter and the payment flows."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

GOOD_SECRET = "sk_test_" + "a1b2c3d4e5" * 4
GOOD_PUBLIC = "pk_test_" + "f6e5d4c3b2" * 4
BASE_URL = "https://api.paystack.test"


@dataclass
class Txn:
    reference: str
    amount: int
    currency: str
    email: str
    status: str = "abandoned"  # what Paystack reports before the payer pays
    paid_amount: int | None = None
    paid_currency: str | None = None
    id: int = 0


@dataclass
class FakePaystack:
    secret: str = GOOD_SECRET
    txns: dict[str, Txn] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    down: bool = False
    next_id: int = 5000

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    # ---- test controls -------------------------------------------------------------------

    def pay(
        self, reference: str, *, amount: int | None = None, currency: str | None = None
    ) -> None:
        txn = self.txns[reference]
        txn.status = "success"
        txn.paid_amount = txn.amount if amount is None else amount
        txn.paid_currency = currency or txn.currency

    def decline(self, reference: str) -> None:
        self.txns[reference].status = "failed"

    def webhook(self, reference: str, *, event: str = "charge.success") -> tuple[bytes, str]:
        body = json.dumps({"event": event, "data": {"reference": reference}}).encode()
        return body, sign(body, self.secret)

    # ---- the API -------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if self.down:
            return httpx.Response(503, json={"status": False, "message": "down"})
        if request.headers.get("authorization") != f"Bearer {self.secret}":
            return httpx.Response(401, json={"status": False, "message": "Invalid key"})
        if request.method == "GET" and path == "/transaction":
            return _ok([])
        if request.method == "POST" and path == "/transaction/initialize":
            body: dict[str, Any] = json.loads(request.content)
            ref = body["reference"]
            if ref in self.txns:
                return httpx.Response(400, json={"status": False, "message": "Duplicate"})
            self.next_id += 1
            self.txns[ref] = Txn(
                reference=ref,
                amount=int(body["amount"]),
                currency=body["currency"],
                email=body["email"],
                id=self.next_id,
            )
            return _ok({"authorization_url": f"https://checkout.paystack.com/{ref.lower()}"})
        if request.method == "GET" and path.startswith("/transaction/verify/"):
            ref = path.rsplit("/", 1)[1]
            txn = self.txns.get(ref)
            if txn is None:
                return httpx.Response(
                    400, json={"status": False, "message": "Transaction reference not found"}
                )
            return _ok(
                {
                    "id": txn.id,
                    "reference": ref,
                    "status": txn.status,
                    "amount": txn.paid_amount if txn.paid_amount is not None else txn.amount,
                    "currency": txn.paid_currency or txn.currency,
                    "channel": "card",
                    "gateway_response": "Approved" if txn.status == "success" else "Declined",
                }
            )
        return httpx.Response(404, json={"status": False, "message": "no route"})


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()


def _ok(data: Any) -> httpx.Response:
    return httpx.Response(200, json={"status": True, "message": "ok", "data": data})
