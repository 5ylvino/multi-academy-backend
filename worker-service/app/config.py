from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "multi-academy-worker-service"
    app_env: str = "development"
    port: int = Field(default=8014, alias="PORT")

    service_jwt_secret: str = Field(default="change-me", alias="SERVICE_JWT_SECRET")
    service_jwt_audience: str = Field(default="mas-worker-service", alias="SERVICE_JWT_AUDIENCE")
    service_jwt_issuer: str = Field(default="mas-school-server", alias="SERVICE_JWT_ISSUER")

    school_internal_api_url: str = Field(
        default="http://localhost:8001/api/v1",
        alias="SCHOOL_INTERNAL_API_URL",
    )

    @model_validator(mode="after")
    def validate_production(self) -> "Settings":
        if self.app_env != "production":
            return self
        if self.service_jwt_secret in {"change-me", "change-me-in-production"}:
            raise ValueError("SERVICE_JWT_SECRET must be changed in production")
        if "localhost" in self.school_internal_api_url:
            raise ValueError("SCHOOL_INTERNAL_API_URL must not use localhost in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
