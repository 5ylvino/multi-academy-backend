"""AES-256-GCM encryption for secrets stored in the database, with key rotation.

Ciphertext format: ``<key id>.<base64url(nonce || ciphertext || tag)>``. New data is always
sealed with the current key; older keys stay readable until every row is re-sealed.
"""

from __future__ import annotations

import base64
import binascii
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def decode_key(value: str) -> bytes:
    try:
        key = _b64d(value.strip())
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Encryption keys must be base64url-encoded.") from exc
    if len(key) != 32:
        raise ValueError("Encryption keys must be 32 bytes (AES-256).")
    return key


def generate_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


class AesGcmSecretBox:
    def __init__(self, *, current_key_id: str, keys: dict[str, bytes]) -> None:
        if current_key_id not in keys:
            raise ValueError("The current key id must be one of the configured keys.")
        if any("." in kid or not kid for kid in keys):
            raise ValueError("Key ids must be non-empty and contain no dots.")
        self._current = current_key_id
        self._aead = {kid: AESGCM(key) for kid, key in keys.items()}

    def encrypt(self, plaintext: bytes) -> str:
        nonce = secrets.token_bytes(_NONCE_BYTES)
        sealed = self._aead[self._current].encrypt(nonce, plaintext, self._current.encode())
        body = base64.urlsafe_b64encode(nonce + sealed).decode().rstrip("=")
        return f"{self._current}.{body}"

    def decrypt(self, ciphertext: str) -> bytes:
        kid, _, body = ciphertext.partition(".")
        aead = self._aead.get(kid)
        if aead is None or not body:
            raise ValueError("Ciphertext was sealed with an unknown key.")
        raw = _b64d(body)
        try:
            return aead.decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], kid.encode())
        except InvalidTag as exc:
            raise ValueError("Ciphertext failed authentication.") from exc
