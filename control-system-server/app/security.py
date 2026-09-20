import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
import pyotp
from cryptography.fernet import Fernet

from app.config import get_settings

settings = get_settings()


# ---------------------------------------------------------------- passwords

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode()[:72], hashed.encode())
    except ValueError:
        return False


# ---------------------------------------------------------------- staff JWT

def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_staff_token(
    user_id: int,
    email: str,
    role: str,
    permissions: list[str],
    token_type: str = "access",
    *,
    jti: str | None = None,
    session_jti: str | None = None,
) -> str:
    if token_type == "access":
        exp = _now() + timedelta(minutes=settings.access_token_minutes)
    else:
        exp = _now() + timedelta(days=settings.refresh_token_days)
    token_jti = jti or secrets.token_urlsafe(16)
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "permissions": permissions,
        "type": token_type,
        "jti": token_jti,
        "sid": session_jti or token_jti,  # session id = access jti for the pair
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": _now(),
        "exp": exp,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_staff_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )


# ---------------------------------------------------------------- m2m JWT

def create_m2m_token(client_id: str, scopes: list[str]) -> str:
    payload = {
        "sub": client_id,
        "scopes": scopes,
        "type": "m2m",
        "iss": settings.jwt_issuer,
        "aud": settings.m2m_jwt_audience,
        "iat": _now(),
        "exp": _now() + timedelta(minutes=settings.m2m_token_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_m2m_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        issuer=settings.jwt_issuer,
        audience=settings.m2m_jwt_audience,
    )


# ---------------------------------------------------------------- MFA (TOTP)

def generate_totp_secret() -> str:
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, email: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name="Multi-Academy Control")


def verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)


# ------------------------------------------------- provider secret envelope

def _fernet() -> Fernet:
    key = settings.secrets_encryption_key
    if not key:
        if settings.environment != "development":
            raise RuntimeError(
                "SECRETS_ENCRYPTION_KEY is required outside development. Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; "
                'print(Fernet.generate_key().decode())"'
            )
        # Development only: deterministic key derived from the JWT secret so a
        # local database survives restarts without extra configuration.
        digest = hashlib.sha256(f"secrets:{settings.jwt_secret}".encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key)


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


def secret_fingerprint(plain: str) -> str:
    """Non-reversible display hint. The last 4 characters are only revealed for
    secrets long enough that they carry negligible information."""
    digest = hashlib.sha256(plain.encode()).hexdigest()[:8]
    if len(plain) >= 16:
        return f"{digest}…{plain[-4:]}"
    return f"{digest}…"


def generate_client_secret() -> str:
    return secrets.token_urlsafe(32)


_PBKDF2_ITERATIONS = 240_000


def hash_client_secret(plain: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, _PBKDF2_ITERATIONS)
    return (
        f"pbkdf2_sha256${_PBKDF2_ITERATIONS}$"
        f"{base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"
    )


def verify_client_secret(plain: str, stored: str) -> bool:
    """Constant-time verification. Accepts the legacy unsalted sha256 hex format
    so existing service clients keep working until their secret is rotated."""
    if not stored:
        return False
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt_b64, digest_b64 = stored.split("$")
            expected = base64.b64decode(digest_b64)
            actual = hashlib.pbkdf2_hmac(
                "sha256", plain.encode(), base64.b64decode(salt_b64), int(iterations)
            )
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(expected, actual)
    legacy = hashlib.sha256(plain.encode()).hexdigest()
    return hmac.compare_digest(stored, legacy)


def client_secret_needs_rehash(stored: str) -> bool:
    return not stored.startswith("pbkdf2_sha256$")
