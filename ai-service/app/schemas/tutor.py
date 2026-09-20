from pydantic import BaseModel, Field

from app.schemas.common import ProviderConfig
from app.schemas.chat import ChatMessage


class TutorChatRequest(BaseModel):
    message: str
    session_id: str | None = Field(default=None, alias="sessionId")
    subject_id: str | None = Field(default=None, alias="subjectId")
    topic: str | None = None
    provider: ProviderConfig | None = None

    model_config = {"populate_by_name": True}


class TutorQuizOption(BaseModel):
    label: str
    value: str


class TutorQuiz(BaseModel):
    question: str
    options: list[TutorQuizOption]
    correct_value: str = Field(alias="correctValue")

    model_config = {"populate_by_name": True}


class TutorChatResponse(BaseModel):
    session_id: str = Field(alias="sessionId")
    content: str
    state: str
    mastery_pct: int = Field(alias="masteryPct")
    quiz: TutorQuiz | None = None
    provider_id: str = Field(alias="providerId")
    model: str
    disclaimer: str

    model_config = {"populate_by_name": True}


class TutorQuizAnswerRequest(BaseModel):
    session_id: str = Field(alias="sessionId")
    answer: str

    model_config = {"populate_by_name": True}


class TopicMasteryItem(BaseModel):
    topic_key: str = Field(alias="topicKey")
    subject_id: str | None = Field(default=None, alias="subjectId")
    mastery_pct: int = Field(alias="masteryPct")
    attempt_count: int = Field(alias="attemptCount")
    correct_count: int = Field(alias="correctCount")

    model_config = {"populate_by_name": True}


class TopicMasteryResponse(BaseModel):
    student_id: str = Field(alias="studentId")
    items: list[TopicMasteryItem] = Field(default_factory=list)

    model_config = {"populate_by_name": True}
