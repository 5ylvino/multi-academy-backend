"""The Paystack adapter against an in-memory Paystack API."""

from __future__ import annotations

import httpx
import pytest

from app.domain.shared.errors import AuthenticationFailed, ProviderUnavailable, ValidationFailed
from app.infrastructure.providers.payments.paystack import PaystackGateway
from app.infrastructure.providers.payments.registry import GatewayRegistry
from app.use_cases.shared.ports import CheckoutRequest, GatewayCredentials
from tests.fakes.paystack import BASE_URL, GOOD_PUBLIC, GOOD_SECRET, FakePaystack, sign

CREDS = GatewayCredentials(public_key=GOOD_PUBLIC, secret_key=GOOD_SECRET)


def _gateway(fake: FakePaystack) -> PaystackGateway:
    return PaystackGateway(base_url=BASE_URL, client=fake.client())


def _request(reference: str = "MAS-ABCDEFGHJKLMNPQRSTUV", currency: str = "NGN") -> CheckoutRequest:
    return CheckoutRequest(
        reference=reference,
        amount_minor=2_500_000,
        currency=currency,
        email="parent@school.ng",
        callback_url="https://school.mas.ng/fees/payments/return",
        metadata={"student_id": "s1"},
    )


def test_key_modes() -> None:
    gw = PaystackGateway(base_url=BASE_URL, client=httpx.AsyncClient())
    assert gw.key_mode(GOOD_SECRET) == "test"
    assert gw.key_mode("sk_live_" + "x" * 40) == "live"
    with pytest.raises(ValidationFailed):
        gw.key_mode("pk_test_" + "x" * 40)
    with pytest.raises(ValidationFailed):
        gw.key_mode("sk_test_short")


async def test_check_credentials() -> None:
    fake = FakePaystack()
    gw = _gateway(fake)
    await gw.check_credentials(CREDS)
    with pytest.raises(ValidationFailed, match="didn't accept"):
        await gw.check_credentials(
            GatewayCredentials(public_key=GOOD_PUBLIC, secret_key="sk_test_" + "z" * 40)
        )
    with pytest.raises(ValidationFailed, match="different modes"):
        await gw.check_credentials(
            GatewayCredentials(public_key="pk_live_" + "z" * 40, secret_key=GOOD_SECRET)
        )
    fake.down = True
    with pytest.raises(ProviderUnavailable):
        await gw.check_credentials(CREDS)


async def test_initialize_and_verify() -> None:
    fake = FakePaystack()
    gw = _gateway(fake)
    session = await gw.initialize(CREDS, _request())
    assert session.authorization_url.startswith("https://checkout.paystack.com/")
    assert fake.txns["MAS-ABCDEFGHJKLMNPQRSTUV"].amount == 2_500_000

    pending = await gw.verify(CREDS, "MAS-ABCDEFGHJKLMNPQRSTUV")
    assert pending.status == "abandoned"
    fake.pay("MAS-ABCDEFGHJKLMNPQRSTUV")
    paid = await gw.verify(CREDS, "MAS-ABCDEFGHJKLMNPQRSTUV")
    assert (paid.status, paid.amount_minor, paid.currency) == ("success", 2_500_000, "NGN")
    assert paid.transaction_id is not None
    assert paid.channel == "card"

    unknown = await gw.verify(CREDS, "MAS-NEVERSEEN")
    assert unknown.status == "pending"

    with pytest.raises(ValidationFailed, match="can't take payments in"):
        await gw.initialize(CREDS, _request(currency="JPY"))


async def test_reads_retry_once_but_payments_never_do() -> None:
    fake = FakePaystack()
    flaky = {"left": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        if flaky["left"] > 0:
            flaky["left"] -= 1
            return httpx.Response(502)
        return fake.handle(request)

    gw = PaystackGateway(
        base_url=BASE_URL, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    await gw.check_credentials(CREDS)  # retried after one 502
    flaky["left"] = 1
    with pytest.raises(ProviderUnavailable):
        await gw.initialize(CREDS, _request())  # not retried
    assert "MAS-ABCDEFGHJKLMNPQRSTUV" not in fake.txns


def test_webhook_signature() -> None:
    gw = PaystackGateway(base_url=BASE_URL, client=httpx.AsyncClient())
    body = b'{"event":"charge.success","data":{"reference":"MAS-1"}}'
    notice = gw.read_webhook(CREDS, {"x-paystack-signature": sign(body, GOOD_SECRET)}, body)
    assert (notice.event, notice.reference) == ("charge.success", "MAS-1")
    with pytest.raises(AuthenticationFailed):
        gw.read_webhook(CREDS, {"x-paystack-signature": sign(body, "sk_test_other")}, body)
    with pytest.raises(AuthenticationFailed):
        gw.read_webhook(CREDS, {}, body)
    with pytest.raises(ValidationFailed):
        gw.read_webhook(CREDS, {"x-paystack-signature": sign(b"nope", GOOD_SECRET)}, b"nope")


def test_registry() -> None:
    registry = GatewayRegistry([PaystackGateway(base_url=BASE_URL, client=httpx.AsyncClient())])
    assert registry.providers() == ["paystack"]
    with pytest.raises(ValidationFailed):
        registry.get("flutterwave")
