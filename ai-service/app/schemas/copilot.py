from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ProviderConfig
from app.schemas.insights import TopicHeatmapCell


class CopilotProposal(BaseModel):
    type: str
    title: str
    detail: str | None = None
    requires_approval: bool = Field(default=True, alias="requiresApproval")

    model_config = {"populate_by_name": True}


class InterventionPlanRequest(BaseModel):
    class_id: str = Field(alias="classId")
    subject_id: str | None = Field(default=None, alias="subjectId")
    topic: str
    class_name: str | None = Field(default=None, alias="className")
    subject_name: str | None = Field(default=None, alias="subjectName")
    weak_topic: TopicHeatmapCell | None = Field(default=None, alias="weakTopic")
    provider: ProviderConfig | None = None

    model_config = {"populate_by_name": True}


class InterventionPlanBody(BaseModel):
    reteach_suggestion: str = Field(alias="reteachSuggestion")
    question_count: int = Field(alias="questionCount")
    small_group_recommendation: str = Field(alias="smallGroupRecommendation")
    parent_coaching_note: str = Field(alias="parentCoachingNote")
    proposals: list[CopilotProposal] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class InterventionPlanResponse(BaseModel):
    plan_id: str = Field(alias="planId")
    status: str
    class_id: str = Field(alias="classId")
    subject_id: str | None = Field(default=None, alias="subjectId")
    topic: str
    plan: InterventionPlanBody
    disclaimer: str

    model_config = {"populate_by_name": True}


class InterventionApproveRequest(BaseModel):
    action: str = "approve"

    model_config = {"populate_by_name": True}


class InterventionListItem(BaseModel):
    plan_id: str = Field(alias="planId")
    status: str
    class_id: str = Field(alias="classId")
    topic: str
    created_at: str | None = Field(default=None, alias="createdAt")

    model_config = {"populate_by_name": True}


class IngestBatchItem(BaseModel):
    source_type: str = Field(alias="sourceType")
    source_id: str = Field(alias="sourceId")
    title: str | None = None
    text: str
    visibility: str = "staff"
    subject_id: str | None = Field(default=None, alias="subjectId")
    class_id: str | None = Field(default=None, alias="classId")

    model_config = {"populate_by_name": True}


class IngestBatchRequest(BaseModel):
    items: list[IngestBatchItem] = Field(default_factory=list)


class IngestBatchResponse(BaseModel):
    ingested: int
    failed: int
    document_ids: list[str] = Field(default_factory=list, alias="documentIds")

    model_config = {"populate_by_name": True}
