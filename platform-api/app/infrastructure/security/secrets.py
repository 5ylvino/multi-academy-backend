"""Opaque secrets and keyed hashing for them."""

from __future__ import annotations

import hashlib
import hmac
import secrets


class HmacSecretHasher:
    """HMAC-SHA256 with a server-side pepper. Right for high-entropy secrets, not for passwords."""

    def __init__(self, pepper: bytes) -> None:
        if len(pepper) < 16:
            raise ValueError("Pepper must be at least 16 bytes.")
        self._pepper = pepper

    def hash(self, secret: str) -> str:
        return hmac.new(self._pepper, secret.encode(), hashlib.sha256).hexdigest()

    def matches(self, secret: str, secret_hash: str) -> bool:
        return hmac.compare_digest(self.hash(secret), secret_hash)


class CryptoSecretGenerator:
    def token(self) -> str:
        return secrets.token_urlsafe(32)

    def numeric_code(self, digits: int) -> str:
        return self.code_from_alphabet("0123456789", digits)

    def random_bytes(self, length: int) -> bytes:
        return secrets.token_bytes(length)

    def code_from_alphabet(self, alphabet: str, length: int) -> str:
        return "".join(secrets.choice(alphabet) for _ in range(length))
