from functools import lru_cache

from app.config import Settings, get_settings
from app.providers.llm.registry import LlmRegistry
from app.providers.pinecone.client import PineconeClient
from app.providers.storage.s3 import ObjectStorageClient


@lru_cache
def get_llm_registry() -> LlmRegistry:
    return LlmRegistry(get_settings())


@lru_cache
def get_pinecone_client() -> PineconeClient:
    return PineconeClient(get_settings())


@lru_cache
def get_storage_client() -> ObjectStorageClient:
    return ObjectStorageClient(get_settings())


def get_app_settings() -> Settings:
    return get_settings()
