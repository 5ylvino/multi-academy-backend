import json
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values shipped in .env.example / code defaults. Running with any of these
# outside development is a deployment mistake, not a configuration choice.
# Include every historically published example so renaming the example file
# cannot silently bypass the guard.
INSECURE_DEFAULTS = {
    "jwt_secret": "change-me-control-plane-secret-32b!",
    "bootstrap_m2m_client_secret": "dev-nest-m2m-secret-change-me-in-prod",
    "nest_webhook_secret": "dev-control-webhook-secret-change-me",
}

# Additional known-insecure JWT secrets that have appeared in docs/example files.
INSECURE_JWT_SECRETS = frozenset(
    {
        INSECURE_DEFAULTS["jwt_secret"],
        "dev-control-jwt-secret-min-32-bytes!!",
        "change-me-control-plane-secret-32b!",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        enable_decoding=False,
    )

    app_name: str = "Multi-Academy Control System"
    environment: str = "development"  # development | staging | production
    debug: bool = True

    # Postgres (Neon) in real deployments; SQLite fallback keeps local dev zero-config.
    database_url: str = "sqlite:///./control.db"
    # Neon / Postgres pool. Ignored for SQLite. Keep modest — each replica
    # holds pool_size + max_overflow connections against the same project.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle_seconds: int = 1800
    db_pool_timeout_seconds: int = 10
    db_statement_timeout_ms: int = 15000

    # Staff auth
    jwt_secret: str = "change-me-control-plane-secret-32b!"
    jwt_issuer: str = "multi-academy-control"
    jwt_audience: str = "control-staff"
    access_token_minutes: int = 30
    refresh_token_days: int = 7
    # Soft idle hint for staff UI; JWT access TTL remains ACCESS_TOKEN_MINUTES.
    staff_idle_timeout_minutes: int = 30

    # m2m (school Nest servers)
    m2m_jwt_audience: str = "control-m2m"
    m2m_token_minutes: int = 15

    # Separate rate-limit budgets (requests / rolling 60s per client IP)
    rate_limit_staff_per_minute: int = 120
    rate_limit_m2m_per_minute: int = 300
    rate_limit_auth_per_minute: int = 20

    # IP allowlist: env override; DB SecuritySettings can also enforce.
    ip_allowlist_enforced: bool = False

    # Number of trusted reverse proxies in front of the app. X-Forwarded-For is
    # ignored when this is 0, because otherwise any caller can spoof their source
    # IP and defeat both the allowlist and their own rate-limit bucket. Set to 1
    # behind a single load balancer, 2 behind Cloudflare + a load balancer, etc.
    trusted_proxy_count: int = 0

    # Invoice PDF branding
    invoice_issuer_name: str = "Multi-Academy Platform"

    # Redis (optional — rate-limit/outbox workers; in-memory fallback used today)
    redis_url: str = ""

    # Outbox invalidation worker — POST events to Nest webhook when set.
    nest_config_webhook_url: str = ""
    nest_webhook_secret: str = ""
    outbox_worker_interval_seconds: int = 15
    # SaaS invoice generation + dunning pass interval
    billing_worker_interval_seconds: int = 300
    billing_worker_enabled: bool = True
    # Days between past_due → grace (and the grace window length). Used by
    # advance_dunning / run_dunning_pass; was previously a hard-coded 7.
    dunning_grace_days: int = 7

    # Envelope encryption key for provider secrets (Fernet, urlsafe base64 32 bytes).
    # Required outside development — startup fails without it.
    secrets_encryption_key: str = ""

    # create_all() on boot is convenient locally but hides Alembic drift. Set
    # false in staging/production so migrations are the only schema authority.
    auto_create_schema: bool = True
    worker_shutdown_timeout_seconds: float = 20.0

    # Sentry DSN — error reporting is skipped when empty.
    sentry_dsn: str = ""
    log_json: bool = False

    # Leader election for the in-process billing/outbox workers. With more than
    # one replica, exactly one should run them; the others set this false.
    workers_enabled: bool = True

    # No built-in origins: every deployment must provide CORS_ORIGINS.
    cors_origins: list[str] = []

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> list[str]:
        """Accept JSON or comma-separated origins and normalize their shape."""
        if isinstance(value, str):
            raw = value.strip()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw.split(",")
            value = parsed
        if not isinstance(value, (list, tuple)):
            raise ValueError("CORS_ORIGINS must be a JSON array or comma-separated list")

        origins = [
            str(origin).strip().rstrip("/")
            for origin in value
            if str(origin).strip().rstrip("/")
        ]
        if not origins:
            raise ValueError("CORS_ORIGINS must contain at least one origin")
        return origins

    # Runtime config cache hints for the school server
    runtime_config_ttl_seconds: int = 60

    # Bootstrap owner credentials are synchronized by the startup seed.
    bootstrap_owner_email: str = ""
    bootstrap_owner_password: str = ""

    # First-boot Nest m2m client (created when client_id does not exist)
    bootstrap_m2m_client_id: str = "svc_nest_dev_local"
    bootstrap_m2m_client_secret: str = "dev-nest-m2m-secret-change-me-in-prod"
    bootstrap_m2m_name: str = "Nest school server (local)"

    # Phase C — dual-control for destructive actions (auto-on in production).
    # When false, high-risk actions execute immediately (local/dev convenience).
    dual_control_required: bool | None = None
    dual_control_expiry_hours: int = 24
    # Public status page branding
    status_page_title: str = "Multi-Academy Status"
    status_page_url: str = "http://localhost:8000/public/v1/status"

    @model_validator(mode="after")
    def _validate_production(self) -> "Settings":
        if self.environment == "development":
            return self

        problems: list[str] = []
        for field, insecure in INSECURE_DEFAULTS.items():
            if getattr(self, field, None) == insecure:
                problems.append(f"{field.upper()} is still the example value")
        if self.jwt_secret in INSECURE_JWT_SECRETS:
            problems.append(
                "JWT_SECRET is a published example value — generate a unique secret "
                "before going live"
            )
        if not self.secrets_encryption_key:
            problems.append(
                "SECRETS_ENCRYPTION_KEY is unset — provider credentials would be "
                "encrypted with a key derived from JWT_SECRET"
            )
        if len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be at least 32 characters")
        if not self.cors_origins:
            problems.append("CORS_ORIGINS must contain at least one allowed origin")
        if self.database_url.startswith("sqlite"):
            problems.append(
                "DATABASE_URL points at SQLite — set a Postgres URL for staging/production"
            )
        if self.debug:
            problems.append("DEBUG must be false in staging/production")
        if self.workers_enabled and not self.redis_url.strip():
            problems.append(
                "REDIS_URL is required when WORKERS_ENABLED=true in staging/production"
            )
        if any(
            o.startswith("http://") and "localhost" not in o for o in self.cors_origins
        ):
            problems.append("CORS_ORIGINS contains a plaintext http:// origin")
        if "*" in self.cors_origins:
            problems.append(
                "CORS_ORIGINS cannot be a wildcard when credentials are allowed"
            )

        if problems:
            raise ValueError(
                f"Refusing to start in {self.environment!r} with insecure configuration:\n  - "
                + "\n  - ".join(problems)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
