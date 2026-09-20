from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "multi-academy-ai-service"
    app_env: str = "development"
    log_level: str = "INFO"

    service_jwt_secret: str = Field(default="change-me", alias="SERVICE_JWT_SECRET")
    service_jwt_audience: str = Field(default="mas-ai-service", alias="SERVICE_JWT_AUDIENCE")
    service_jwt_issuer: str = Field(default="mas-school-server", alias="SERVICE_JWT_ISSUER")

    school_server_base_url: str = Field(default="http://localhost:8001", alias="SCHOOL_SERVER_BASE_URL")
    school_server_m2m_token: str = Field(default="", alias="SCHOOL_SERVER_M2M_TOKEN")

    database_url: str = Field(
        default="postgresql+psycopg://localhost/mas_ai",
        alias="DATABASE_URL",
    )

    pinecone_api_key: str = Field(default="", alias="PINECONE_API_KEY")
    pinecone_index_name: str = Field(default="mas-ai", alias="PINECONE_INDEX_NAME")
    pinecone_namespace_prefix: str = Field(default="tenant", alias="PINECONE_NAMESPACE_PREFIX")

    llm_provider: str = Field(default="nvidia", alias="LLM_PROVIDER")
    llm_model: str = Field(
        default="nvidia/llama-3.1-nemotron-70b-instruct",
        alias="LLM_MODEL",
    )
    llm_temperature: float = Field(default=0.3, alias="LLM_TEMPERATURE")
    llm_max_tokens: int = Field(default=2048, alias="LLM_MAX_TOKENS")

    nvidia_api_key: str = Field(default="", alias="NVIDIA_API_KEY")
    kilo_api_key: str = Field(default="", alias="KILO_API_KEY")
    kilo_base_url: str = Field(
        default="https://api.kilo.ai/api/gateway",
        alias="KILO_BASE_URL",
    )

    embedding_provider: str = Field(default="nvidia", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="nvidia/nv-embedqa-e5-v5", alias="EMBEDDING_MODEL")
    embedding_dimension: int = Field(default=1024, alias="EMBEDDING_DIMENSION")

    object_storage_bucket: str = Field(default="mas-ai-documents", alias="OBJECT_STORAGE_BUCKET")
    object_storage_endpoint: str = Field(default="", alias="OBJECT_STORAGE_ENDPOINT")
    object_storage_region: str = Field(default="auto", alias="OBJECT_STORAGE_REGION")
    object_storage_access_key: str = Field(default="", alias="OBJECT_STORAGE_ACCESS_KEY")
    object_storage_secret_key: str = Field(default="", alias="OBJECT_STORAGE_SECRET_KEY")

    max_messages_per_session: int = Field(default=40, alias="MAX_MESSAGES_PER_SESSION")
    max_message_chars: int = Field(default=8000, alias="MAX_MESSAGE_CHARS")
    max_rag_chunks: int = Field(default=12, alias="MAX_RAG_CHUNKS")
    request_timeout_seconds: int = Field(default=45, alias="REQUEST_TIMEOUT_SECONDS")

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
