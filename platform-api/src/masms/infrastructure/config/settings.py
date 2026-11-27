"""Runtime settings, loaded from environment variables (prefix ``MASMS_``)."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MASMS_", env_file=".env", extra="ignore", populate_by_name=True
    )

    environment: Environment = Environment.DEVELOPMENT
    app_name: str = "MA-SMS Platform API"
    log_level: str = "INFO"
    log_json: bool = True

    # Database. The app role must NOT be a superuser, or row-level security is bypassed.
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://masms_app:masms_app@localhost:5432/masms"
    )
    database_read_url: SecretStr | None = None
    database_pool_size: int = Field(default=20, ge=1, le=200)
    database_max_overflow: int = Field(default=10, ge=0, le=200)
    database_pool_timeout: float = 5.0
    database_statement_timeout_ms: int = 15_000
    #: Set behind PgBouncer in transaction mode (disables the prepared-statement cache).
    database_pgbouncer: bool = False

    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")

    # Tokens: Ed25519 private key (PEM). Previous public keys keep old tokens valid in rotation.
    jwt_private_key_pem: SecretStr | None = None
    jwt_key_id: str = "k1"
    jwt_previous_public_keys: dict[str, str] = Field(default_factory=dict)
    jwt_issuer: str = "https://api.mas.ng"
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=90)
    platform_refresh_token_ttl_hours: int = Field(default=12, ge=1, le=72)
    #: HMAC key for refresh tokens and verification codes. 32+ random bytes.
    secret_pepper: SecretStr = SecretStr("dev-only-pepper-change-me-0123456789abcdef")

    #: AES-256-GCM keys (base64url, 32 bytes) for secrets read back later, e.g. MFA seeds.
    #: ``data_keys`` maps key id -> key; ``data_key_id`` picks the one new data is sealed with.
    data_keys: dict[str, SecretStr] = Field(default_factory=dict)
    data_key_id: str = "d1"
    #: Owner-console staff must use an authenticator app. Only tests may turn this off.
    platform_mfa_required: bool = True
    mfa_issuer: str = "MA-SMS Console"

    cors_school_origins: list[str] = Field(default_factory=list)
    cors_platform_origins: list[str] = Field(default_factory=list)
    trusted_hosts: list[str] = Field(default_factory=lambda: ["*"])
    #: Number of reverse proxies in front of the app whose X-Forwarded-For we trust.
    trusted_proxy_hops: int = 1

    default_plan_code: str = "starter"
    trial_days: int = 30

    #: Public base URL of the school web app; invite and reset links point here.
    school_app_url: str = "http://localhost:3000"

    email_provider: str = "log"  # "log" | "smtp"
    email_from_address: str = "no-reply@mas.ng"
    email_from_name: str = "MA-SMS"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True

    # Private files (payment receipts, photos). Production uses Neon Object Storage, which is
    # S3-compatible: endpoint, region and keys come from the branch's storage credential.
    object_store: str = "local"  # "local" | "s3"
    object_store_path: str = "./var/objects"
    # The AWS_* names are what `neon env pull` writes, so Neon's values work unchanged.
    object_store_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MASMS_OBJECT_STORE_ENDPOINT", "AWS_ENDPOINT_URL_S3"),
    )
    object_store_region: str = Field(
        default="us-east-2",
        validation_alias=AliasChoices("MASMS_OBJECT_STORE_REGION", "AWS_REGION"),
    )
    object_store_bucket: str = "masms-private"
    object_store_access_key_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MASMS_OBJECT_STORE_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"),
    )
    object_store_secret_access_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MASMS_OBJECT_STORE_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"
        ),
    )

    metrics_enabled: bool = True
    docs_enabled: bool = True

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def is_production(self) -> bool:
        return self.environment in {Environment.PRODUCTION, Environment.STAGING}

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if not self.is_production:
            return self
        problems: list[str] = []
        if self.jwt_private_key_pem is None:
            problems.append("MASMS_JWT_PRIVATE_KEY_PEM is required")
        if (
            "dev-only" in self.secret_pepper.get_secret_value()
            or len(self.secret_pepper.get_secret_value()) < 32
        ):
            problems.append("MASMS_SECRET_PEPPER must be 32+ random characters")
        if self.data_key_id not in self.data_keys:
            problems.append("MASMS_DATA_KEYS must include MASMS_DATA_KEY_ID")
        if not self.platform_mfa_required:
            problems.append("MASMS_PLATFORM_MFA_REQUIRED cannot be off")
        if "*" in self.trusted_hosts:
            problems.append("MASMS_TRUSTED_HOSTS must list real host names")
        if self.email_provider == "log":
            problems.append("MASMS_EMAIL_PROVIDER must be a real provider")
        if self.object_store != "s3":
            problems.append("MASMS_OBJECT_STORE must be s3 (Neon Object Storage)")
        elif not (
            self.object_store_endpoint
            and self.object_store_access_key_id
            and self.object_store_secret_access_key
        ):
            problems.append("MASMS_OBJECT_STORE_ENDPOINT and its access keys are required")
        if problems:
            raise ValueError("Unsafe production settings: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
