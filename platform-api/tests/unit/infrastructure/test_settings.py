from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from app.infrastructure.config.settings import Environment, Settings
from app.infrastructure.security.secret_box import generate_key
from app.infrastructure.security.tokens import generate_private_key_pem


def _production(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "environment": Environment.PRODUCTION,
        "jwt_private_key_pem": SecretStr(generate_private_key_pem()),
        "secret_pepper": SecretStr("p" * 40),
        "trusted_hosts": ["api.mas.ng"],
        "email_provider": "smtp",
        "data_keys": {"d1": SecretStr(generate_key())},
        "data_key_id": "d1",
        "object_store": "s3",
        "public_api_url": "https://api.mas.ng",
        "payments_allow_test_keys": False,
        "object_store_endpoint": "https://br-x.storage.c-2.us-east-2.aws.neon.tech",
        "object_store_access_key_id": "nak_live_x",
        "object_store_secret_access_key": SecretStr("nsk_live_x"),
        "platform_bank_transfer_details": "GTBank 0123456789, MA-SMS Ltd",
    }
    base.update(overrides)
    return Settings(**base)


def test_safe_production_settings_load() -> None:
    assert _production().platform_mfa_required is True


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"data_keys": {}}, "MASMS_DATA_KEYS"),
        ({"platform_mfa_required": False}, "MFA_REQUIRED"),
        ({"jwt_private_key_pem": None}, "JWT_PRIVATE_KEY"),
        ({"secret_pepper": SecretStr("dev-only-short")}, "PEPPER"),
        ({"trusted_hosts": ["*"]}, "TRUSTED_HOSTS"),
        ({"email_provider": "log"}, "EMAIL_PROVIDER"),
        ({"object_store": "local"}, "OBJECT_STORE must be s3"),
        ({"object_store_endpoint": None}, "OBJECT_STORE_ENDPOINT"),
    ],
)
def test_unsafe_production_settings_are_refused(override: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _production(**override)


def test_dev_key_seed_is_deterministic() -> None:
    seed = b"s" * 32
    assert generate_private_key_pem(seed) == generate_private_key_pem(seed)
    assert generate_private_key_pem() != generate_private_key_pem()


def test_neon_env_names_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "https://br-y.storage.c-2.us-east-2.aws.neon.tech")
    monkeypatch.setenv("AWS_REGION", "eu-central-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "nak_live_y")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "nsk_live_y")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.object_store_endpoint == "https://br-y.storage.c-2.us-east-2.aws.neon.tech"
    assert settings.object_store_region == "eu-central-1"
    assert settings.object_store_access_key_id == "nak_live_y"
    assert settings.object_store_secret_access_key is not None
    assert settings.object_store_secret_access_key.get_secret_value() == "nsk_live_y"


def test_production_refuses_test_payment_keys_and_plain_http() -> None:
    with pytest.raises(ValueError, match="PAYMENTS_ALLOW_TEST_KEYS"):
        _production(payments_allow_test_keys=True)
    with pytest.raises(ValueError, match="PUBLIC_API_URL"):
        _production(public_api_url="http://api.mas.ng")


def test_production_platform_billing_needs_a_way_to_pay() -> None:
    with pytest.raises(ValueError, match="BANK_TRANSFER_DETAILS"):
        _production(platform_bank_transfer_details=None)
    with pytest.raises(ValueError, match="both platform Paystack keys"):
        _production(platform_paystack_secret_key=SecretStr("sk_live_" + "x" * 40))
    with pytest.raises(ValueError, match="live key"):
        _production(
            platform_paystack_secret_key=SecretStr("sk_test_" + "x" * 40),
            platform_paystack_public_key="pk_test_" + "x" * 40,
        )
    live = _production(
        platform_bank_transfer_details=None,
        platform_paystack_secret_key=SecretStr("sk_live_" + "x" * 40),
        platform_paystack_public_key="pk_live_" + "x" * 40,
    )
    assert live.effective_platform_pay_page_url.endswith("/billing/pay")
