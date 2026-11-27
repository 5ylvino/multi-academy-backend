from __future__ import annotations

import pytest

from masms.infrastructure.security.secret_box import AesGcmSecretBox, decode_key, generate_key


def _box(current: str = "k1", **keys: str) -> AesGcmSecretBox:
    return AesGcmSecretBox(
        current_key_id=current, keys={kid: decode_key(v) for kid, v in keys.items()}
    )


def test_round_trip_and_fresh_nonce() -> None:
    box = _box(k1=generate_key())
    a, b = box.encrypt(b"seed"), box.encrypt(b"seed")
    assert a != b
    assert a.startswith("k1.")
    assert box.decrypt(a) == b"seed"


def test_tampering_and_unknown_keys_are_rejected() -> None:
    box = _box(k1=generate_key())
    sealed = box.encrypt(b"seed")
    flipped = sealed[:-2] + ("A" if sealed[-2] != "A" else "B") + sealed[-1]
    with pytest.raises(ValueError, match="authentication"):
        box.decrypt(flipped)
    with pytest.raises(ValueError, match="unknown key"):
        box.decrypt("k9." + sealed.split(".", 1)[1])
    with pytest.raises(ValueError, match="authentication"):
        # A different key under the same id fails authentication.
        _box("k1", k1=generate_key(), k2=generate_key()).decrypt(sealed)


def test_rotation_reads_old_and_writes_new() -> None:
    old, new = generate_key(), generate_key()
    sealed_old = _box("k1", k1=old).encrypt(b"seed")
    rotated = _box("k2", k1=old, k2=new)
    assert rotated.decrypt(sealed_old) == b"seed"
    assert rotated.encrypt(b"seed").startswith("k2.")


def test_bad_configuration() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        decode_key("c2hvcnQ")
    with pytest.raises(ValueError, match="current key"):
        _box("k2", k1=generate_key())
