from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "multi-academy-tutoring-service"
    app_env: str = "development"

    service_jwt_secret: str = Field(default="change-me", alias="SERVICE_JWT_SECRET")
    service_jwt_audience: str = Field(default="mas-tutoring-service", alias="SERVICE_JWT_AUDIENCE")
    service_jwt_issuer: str = Field(default="mas-school-server", alias="SERVICE_JWT_ISSUER")

    database_url: str = Field(
        default="postgresql+psycopg://localhost/mas_tutoring",
        alias="DATABASE_URL",
    )

    ai_service_url: str = Field(default="", alias="AI_SERVICE_URL")
    ai_service_jwt_secret: str = Field(default="", alias="AI_SERVICE_JWT_SECRET")

    control_api_url: str = Field(default="", alias="CONTROL_API_URL")
    control_m2m_client_id: str = Field(default="", alias="CONTROL_M2M_CLIENT_ID")
    control_m2m_client_secret: str = Field(default="", alias="CONTROL_M2M_CLIENT_SECRET")
    control_secrets_ttl_seconds: int = Field(default=60, alias="CONTROL_SECRETS_TTL_SECONDS")

    platform_fee_percent: float = Field(default=15.0, alias="PLATFORM_FEE_PERCENT")
    default_currency: str = Field(default="NGN", alias="DEFAULT_CURRENCY")

    @model_validator(mode="after")
    def validate_production(self) -> "Settings":
        if self.app_env != "production":
            return self
        if self.service_jwt_secret in {"change-me", "change-me-in-production"}:
            raise ValueError("SERVICE_JWT_SECRET must be changed in production")
        if self.database_url.startswith(("sqlite", "postgresql+psycopg://localhost")):
            raise ValueError("DATABASE_URL must point to a managed production database")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
