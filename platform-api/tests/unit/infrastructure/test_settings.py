from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from masms.infrastructure.config.settings import Environment, Settings
from masms.infrastructure.security.secret_box import generate_key
from masms.infrastructure.security.tokens import generate_private_key_pem


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
        "object_store_endpoint": "https://br-x.storage.c-2.us-east-2.aws.neon.tech",
        "object_store_access_key_id": "nak_live_x",
        "object_store_secret_access_key": SecretStr("nsk_live_x"),
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
