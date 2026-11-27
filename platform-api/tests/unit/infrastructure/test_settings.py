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
    ],
)
def test_unsafe_production_settings_are_refused(override: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _production(**override)


def test_dev_key_seed_is_deterministic() -> None:
    seed = b"s" * 32
    assert generate_private_key_pem(seed) == generate_private_key_pem(seed)
    assert generate_private_key_pem() != generate_private_key_pem()
