from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    provider_id: str | None = Field(default=None, alias="providerId")
    model: str | None = None
    max_tokens: int | None = Field(default=None, alias="maxTokens")

    model_config = {"populate_by_name": True}


class UsageInfo(BaseModel):
    prompt_tokens: int | None = Field(default=None, alias="promptTokens")
    completion_tokens: int | None = Field(default=None, alias="completionTokens")

    model_config = {"populate_by_name": True}


class SourceCitation(BaseModel):
    type: str
    id: str
    chunk_id: str | None = Field(default=None, alias="chunkId")
    excerpt: str | None = None

    model_config = {"populate_by_name": True}


class AiResponse(BaseModel):
    content: str
    provider_id: str = Field(alias="providerId")
    model: str
    usage: UsageInfo | None = None
    sources: list[SourceCitation] = Field(default_factory=list)
    disclaimer: str | None = None
    conversation_id: str | None = Field(default=None, alias="conversationId")

    model_config = {"populate_by_name": True}
