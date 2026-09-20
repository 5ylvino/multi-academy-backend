import hashlib
import hmac
import json

from app.providers.paystack import PaystackAdapter


def test_paystack_webhook_signature():
    adapter = PaystackAdapter()
    body = json.dumps({"event": "charge.success", "data": {"reference": "ref-1", "status": "success", "amount": 500000}}).encode()
    secret = "sk_test_secret"
    sig = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    assert adapter.verify_webhook_signature({"secret_key": secret}, {"x-paystack-signature": sig}, body)


def test_paystack_parse_webhook_success():
    adapter = PaystackAdapter()
    body = json.dumps(
        {"event": "charge.success", "data": {"reference": "ref-1", "status": "success", "amount": 250000, "currency": "NGN"}}
    ).encode()
    parsed = adapter.parse_webhook(body)
    assert parsed.reference == "ref-1"
    assert parsed.status == "success"
    assert parsed.amount_minor == 250000
