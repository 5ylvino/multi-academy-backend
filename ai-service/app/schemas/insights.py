from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ProviderConfig


class ClassStudentMastery(BaseModel):
    student_id: str = Field(alias="studentId")
    topic_key: str = Field(alias="topicKey")
    mastery_pct: int = Field(alias="masteryPct")
    subject_id: str | None = Field(default=None, alias="subjectId")

    model_config = {"populate_by_name": True}


class ClassInsightsRequest(BaseModel):
    class_id: str = Field(alias="classId")
    subject_id: str | None = Field(default=None, alias="subjectId")
    mastery_threshold: int = Field(default=60, alias="masteryThreshold")
    student_mastery: list[ClassStudentMastery] = Field(default_factory=list, alias="studentMastery")
    provider: ProviderConfig | None = None

    model_config = {"populate_by_name": True}


class TopicHeatmapCell(BaseModel):
    topic_key: str = Field(alias="topicKey")
    subject_id: str | None = Field(default=None, alias="subjectId")
    student_count: int = Field(alias="studentCount")
    below_threshold_count: int = Field(alias="belowThresholdCount")
    avg_mastery_pct: int = Field(alias="avgMasteryPct")
    severity: str = "low"

    model_config = {"populate_by_name": True}


class InsightFlag(BaseModel):
    topic: str
    subject_id: str | None = Field(default=None, alias="subjectId")
    message: str
    student_count: int = Field(alias="studentCount")
    below_threshold_count: int = Field(alias="belowThresholdCount")
    severity: str = "medium"

    model_config = {"populate_by_name": True}


class ClassInsightsResponse(BaseModel):
    class_id: str = Field(alias="classId")
    heatmap: list[TopicHeatmapCell] = Field(default_factory=list)
    flags: list[InsightFlag] = Field(default_factory=list)
    narration: str | None = None
    disclaimer: str

    model_config = {"populate_by_name": True}


class AtRiskStudentSignal(BaseModel):
    student_id: str = Field(alias="studentId")
    current_avg: float | None = Field(default=None, alias="currentAvg")
    previous_avg: float | None = Field(default=None, alias="previousAvg")
    attendance_rate: float | None = Field(default=None, alias="attendanceRate")
    risk_level: str = Field(default="medium", alias="riskLevel")
    weak_topics: list[str] = Field(default_factory=list, alias="weakTopics")

    model_config = {"populate_by_name": True}


class AtRiskInsightsRequest(BaseModel):
    class_id: str | None = Field(default=None, alias="classId")
    students: list[AtRiskStudentSignal] = Field(default_factory=list)
    provider: ProviderConfig | None = None

    model_config = {"populate_by_name": True}


class AtRiskInsightsResponse(BaseModel):
    count: int
    students: list[dict[str, Any]] = Field(default_factory=list)
    summary: str | None = None
    disclaimer: str

    model_config = {"populate_by_name": True}


class MasteryRollupRequest(BaseModel):
    class_id: str = Field(alias="classId")
    subject_id: str | None = Field(default=None, alias="subjectId")
    mastery_threshold: int = Field(default=60, alias="masteryThreshold")
    student_mastery: list[ClassStudentMastery] = Field(default_factory=list, alias="studentMastery")

    model_config = {"populate_by_name": True}


class MasteryRollupResponse(BaseModel):
    class_id: str = Field(alias="classId")
    topics_processed: int = Field(alias="topicsProcessed")
    rollups: list[TopicHeatmapCell] = Field(default_factory=list)

    model_config = {"populate_by_name": True}
