"""Runtime settings, loaded from environment variables (prefix ``MASMS_``)."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MASMS_", env_file=".env", extra="ignore")

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
        if "*" in self.trusted_hosts:
            problems.append("MASMS_TRUSTED_HOSTS must list real host names")
        if self.email_provider == "log":
            problems.append("MASMS_EMAIL_PROVIDER must be a real provider")
        if problems:
            raise ValueError("Unsafe production settings: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
