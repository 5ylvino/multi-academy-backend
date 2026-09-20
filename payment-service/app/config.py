from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "multi-academy-payment-service"
    app_env: str = "development"
    port: int = Field(default=8012, alias="PORT")

    service_jwt_secret: str = Field(default="change-me", alias="SERVICE_JWT_SECRET")
    service_jwt_audience: str = Field(default="mas-payment-service", alias="SERVICE_JWT_AUDIENCE")
    service_jwt_issuer: str = Field(default="mas-school-server", alias="SERVICE_JWT_ISSUER")

    database_url: str = Field(
        default="postgresql+psycopg://localhost/mas_payments",
        alias="DATABASE_URL",
    )

    control_api_url: str = Field(default="", alias="CONTROL_API_URL")
    control_m2m_client_id: str = Field(default="", alias="CONTROL_M2M_CLIENT_ID")
    control_m2m_client_secret: str = Field(default="", alias="CONTROL_M2M_CLIENT_SECRET")
    control_secrets_ttl_seconds: int = Field(default=60, alias="CONTROL_SECRETS_TTL_SECONDS")

    default_currency: str = Field(default="NGN", alias="DEFAULT_CURRENCY")


@lru_cache
def get_settings() -> Settings:
    return Settings()
