from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "multi-academy-portal-read-service"
    app_env: str = "development"
    port: int = Field(default=8013, alias="PORT")

    service_jwt_secret: str = Field(default="change-me", alias="SERVICE_JWT_SECRET")
    service_jwt_audience: str = Field(default="mas-portal-read-service", alias="SERVICE_JWT_AUDIENCE")
    service_jwt_issuer: str = Field(default="mas-school-server", alias="SERVICE_JWT_ISSUER")
    school_internal_service_jwt_secret: str = Field(
        default="",
        alias="SCHOOL_INTERNAL_SERVICE_JWT_SECRET",
    )

    school_internal_api_url: str = Field(
        default="http://localhost:8001/api/v1",
        alias="SCHOOL_INTERNAL_API_URL",
    )
    redis_url: str = Field(default="", alias="REDIS_URL")
    portal_cache_prefix: str = Field(default="mas:portal:", alias="PORTAL_CACHE_PREFIX")
    portal_cache_ttl_seconds: int = Field(default=30, alias="PORTAL_CACHE_TTL_SECONDS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
