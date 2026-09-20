from pydantic import BaseModel, Field

from app.schemas.common import ProviderConfig
from app.schemas.guidance import StudentSignals


class SubjectReadiness(BaseModel):
    subject_id: str | None = Field(default=None, alias="subjectId")
    subject_name: str = Field(alias="subjectName")
    readiness_pct: int = Field(alias="readinessPct")
    attempt_count: int = Field(alias="attemptCount")
    exam_type: str = Field(default="jamb", alias="examType")
    trend: str | None = None

    model_config = {"populate_by_name": True}


class JambReadinessRequest(BaseModel):
    student_id: str = Field(alias="studentId")
    student_signals: StudentSignals | None = Field(default=None, alias="studentSignals")
    exam_types: list[str] = Field(default_factory=lambda: ["jamb", "waec", "neco"])
    provider: ProviderConfig | None = None

    model_config = {"populate_by_name": True}


class JambReadinessResponse(BaseModel):
    student_id: str = Field(alias="studentId")
    subjects: list[SubjectReadiness] = Field(default_factory=list)
    summary: str | None = None
    disclaimer: str

    model_config = {"populate_by_name": True}
