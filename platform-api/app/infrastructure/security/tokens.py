"""Access tokens: short-lived EdDSA (Ed25519) JWTs with key ids for rotation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.domain.identity.roles import Realm
from app.domain.shared.errors import AuthenticationFailed
from app.use_cases.shared.ports import AccessClaims

_ALGORITHM = "EdDSA"
_LEEWAY_SECONDS = 30


def generate_private_key_pem(seed: bytes | None = None) -> str:
    """A fresh Ed25519 key, or a deterministic one from a 32-byte seed (development only)."""
    key = Ed25519PrivateKey.from_private_bytes(seed) if seed else Ed25519PrivateKey.generate()
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


class JwtTokenService:
    def __init__(
        self,
        *,
        private_key_pem: str,
        key_id: str,
        issuer: str,
        access_ttl_seconds: int,
        previous_public_keys: Mapping[str, str] | None = None,
    ) -> None:
        key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("JWT signing key must be Ed25519.")
        self._private_key = key
        self._key_id = key_id
        self._issuer = issuer
        self._ttl = access_ttl_seconds
        self._public_keys: dict[str, Ed25519PublicKey] = {key_id: key.public_key()}
        for kid, pem in (previous_public_keys or {}).items():
            public = serialization.load_pem_public_key(pem.encode())
            if not isinstance(public, Ed25519PublicKey):
                raise ValueError(f"Previous key {kid} is not Ed25519.")
            self._public_keys[kid] = public

    @property
    def access_ttl_seconds(self) -> int:
        return self._ttl

    def issue_access(self, claims: AccessClaims) -> str:
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "iss": self._issuer,
            "aud": claims.realm.value,
            "sub": str(claims.user_id),
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int(claims.expires_at.timestamp()),
            "jti": claims.token_id,
            "sid": str(claims.session_family),
            "roles": sorted(claims.roles),
        }
        if claims.tenant_id is not None:
            payload["tid"] = str(claims.tenant_id)
        if claims.capabilities:
            payload["caps"] = sorted(claims.capabilities)
        if claims.primary_owner:
            payload["own"] = True
        if claims.tenant_config_version is not None:
            payload["cv"] = claims.tenant_config_version
        return jwt.encode(
            payload, self._private_key, algorithm=_ALGORITHM, headers={"kid": self._key_id}
        )

    def decode_access(self, token: str, *, realm: Realm) -> AccessClaims:
        try:
            header = jwt.get_unverified_header(token)
            key = self._public_keys.get(str(header.get("kid")))
            if key is None:
                raise AuthenticationFailed("Session is not valid.")
            data = jwt.decode(
                token,
                key,
                algorithms=[_ALGORITHM],
                audience=realm.value,
                issuer=self._issuer,
                leeway=_LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "sub", "aud", "iss", "sid", "jti"]},
            )
            tenant_raw = data.get("tid")
            tenant_id = UUID(tenant_raw) if tenant_raw else None
            if (realm is Realm.SCHOOL) != (tenant_id is not None):
                raise AuthenticationFailed("Session is not valid.")
            return AccessClaims(
                realm=realm,
                user_id=UUID(data["sub"]),
                tenant_id=tenant_id,
                roles=frozenset(data.get("roles", [])),
                capabilities=frozenset(data.get("caps", [])),
                primary_owner=bool(data.get("own", False)),
                session_family=UUID(data["sid"]),
                tenant_config_version=data.get("cv"),
                token_id=str(data["jti"]),
                expires_at=datetime.fromtimestamp(int(data["exp"]), UTC),
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationFailed("Session expired.", code="token_expired") from exc
        except (jwt.PyJWTError, ValueError, KeyError) as exc:
            raise AuthenticationFailed("Session is not valid.") from exc
