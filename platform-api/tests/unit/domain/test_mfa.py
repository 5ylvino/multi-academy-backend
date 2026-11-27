from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from masms.domain.identity import totp
from masms.domain.identity.mfa import (
    TotpFactor,
    looks_like_recovery_code,
    normalise_recovery_code,
)
from masms.domain.shared.errors import Conflict, InvalidStateTransition

RFC_SECRET = b"12345678901234567890"


@pytest.mark.parametrize(
    ("unix", "code"),
    [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")],
)
def test_rfc6238_vectors(unix: int, code: str) -> None:
    at = datetime.fromtimestamp(unix, UTC)
    assert totp.code_at(RFC_SECRET, totp.time_step(at)) == code


def test_matching_step_allows_one_step_drift_and_blocks_replay() -> None:
    at = datetime.fromtimestamp(1_700_000_000, UTC)
    now = totp.time_step(at)
    for offset in (-1, 0, 1):
        code = totp.code_at(RFC_SECRET, now + offset)
        assert totp.matching_step(RFC_SECRET, code, at, after_step=None) == now + offset
    assert (
        totp.matching_step(RFC_SECRET, totp.code_at(RFC_SECRET, now + 2), at, after_step=None)
        is None
    )
    used = totp.code_at(RFC_SECRET, now)
    assert totp.matching_step(RFC_SECRET, used, at, after_step=now) is None
    assert totp.matching_step(RFC_SECRET, "12345a", at, after_step=None) is None
    assert totp.matching_step(RFC_SECRET, "1234567", at, after_step=None) is None


def test_provisioning_uri() -> None:
    uri = totp.provisioning_uri(RFC_SECRET, account="ops@mas.ng", issuer="MA-SMS Console")
    assert uri.startswith("otpauth://totp/MA-SMS%20Console%3Aops%40mas.ng?")
    assert "secret=GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ" in uri
    assert "issuer=MA-SMS+Console" in uri


def _factor() -> TotpFactor:
    return TotpFactor.pending(user_id=uuid4(), secret_ciphertext="x", now=datetime.now(UTC))


def test_factor_lifecycle() -> None:
    factor = _factor()
    assert not factor.confirmed
    with pytest.raises(InvalidStateTransition):
        factor.replace_recovery_codes(["a"])
    factor.confirm(step=10, now=datetime.now(UTC), recovery_code_hashes=["h1", "h2"])
    assert factor.confirmed
    with pytest.raises(Conflict):
        factor.confirm(step=11, now=datetime.now(UTC), recovery_code_hashes=[])
    with pytest.raises(InvalidStateTransition):
        factor.use_step(10)
    factor.use_step(11)
    assert factor.consume_recovery_code("h1")
    assert not factor.consume_recovery_code("h1")
    assert factor.recovery_code_hashes == ["h2"]


def test_recovery_code_shape() -> None:
    assert looks_like_recovery_code("abcde-fghjk")
    assert looks_like_recovery_code("ABCDEFGHJK")
    assert not looks_like_recovery_code("123456")
    assert not looks_like_recovery_code("abcde-fghj0")  # 0 isn't in the alphabet
    assert normalise_recovery_code(" AbCdE-fGhJk ") == "abcdefghjk"
