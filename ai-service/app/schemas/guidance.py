from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ProviderConfig


class StudentSignal(BaseModel):
    source: str
    subject: str | None = None
    topic: str | None = None
    score: float | None = None
    max_score: float | None = None
    detail: str | None = None


class StudentSignals(BaseModel):
    student_id: str = Field(alias="studentId")
    weak_topics: list[str] = Field(default_factory=list, alias="weakTopics")
    strong_topics: list[str] = Field(default_factory=list, alias="strongTopics")
    scheme_topics: list[str] = Field(default_factory=list, alias="schemeTopics")
    signals: list[StudentSignal] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class CoachingResource(BaseModel):
    type: str
    title: str
    url: str | None = None
    duration: str | None = None
    vetted: bool = True
    source: str | None = None


class ParentCoachingRequest(BaseModel):
    student_id: str = Field(alias="studentId")
    question: str | None = None
    student_signals: StudentSignals | None = Field(default=None, alias="studentSignals")
    provider: ProviderConfig | None = None

    model_config = {"populate_by_name": True}


class ParentCoachingResponse(BaseModel):
    student_id: str = Field(alias="studentId")
    audience: str = "parent"
    topic: str
    subject: str | None = None
    resources: list[CoachingResource] = Field(default_factory=list)
    tonight_checklist: list[str] = Field(default_factory=list, alias="tonightChecklist")
    at_home: list[str] = Field(default_factory=list, alias="atHome")
    sources: list[str] = Field(default_factory=list)
    disclaimer: str

    model_config = {"populate_by_name": True}


class StudyPlanRequest(BaseModel):
    student_signals: StudentSignals | None = Field(default=None, alias="studentSignals")
    provider: ProviderConfig | None = None

    model_config = {"populate_by_name": True}


class StudyPlanResponse(BaseModel):
    student_id: str = Field(alias="studentId")
    audience: str = "student"
    actions: list[str] = Field(default_factory=list)
    focus_topics: list[str] = Field(default_factory=list, alias="focusTopics")
    mastery_summary: list[dict[str, Any]] = Field(default_factory=list, alias="masterySummary")
    sources: list[str] = Field(default_factory=list)
    source_mode: str = Field(default="ai", alias="sourceMode")
    disclaimer: str

    model_config = {"populate_by_name": True}
