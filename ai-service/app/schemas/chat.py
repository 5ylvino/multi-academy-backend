from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import AiResponse, ProviderConfig


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)
    model: str | None = None
    school_context: dict[str, Any] | str | None = Field(default=None, alias="schoolContext")
    provider: ProviderConfig | None = None

    model_config = {"populate_by_name": True}


class EssayGradeRequest(BaseModel):
    essay_text: str = Field(alias="essayText")
    rubric: str | None = None
    max_score: int = Field(default=100, alias="maxScore")
    model: str | None = None
    provider: ProviderConfig | None = None

    model_config = {"populate_by_name": True}


class EssayGradeResponse(AiResponse):
    score: float | None = None
    feedback: str | None = None
