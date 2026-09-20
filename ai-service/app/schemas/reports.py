from pydantic import BaseModel, Field

from app.schemas.common import ProviderConfig


class ReportCommentRequest(BaseModel):
    student_id: str = Field(alias="studentId")
    term_id: str | None = Field(default=None, alias="termId")
    subject_id: str | None = Field(default=None, alias="subjectId")
    student_name: str | None = Field(default=None, alias="studentName")
    performance_summary: str | None = Field(default=None, alias="performanceSummary")
    locale: str | None = Field(default="en")
    provider: ProviderConfig | None = None

    model_config = {"populate_by_name": True}


class ReportCommentResponse(BaseModel):
    draft_id: str = Field(alias="draftId")
    student_id: str = Field(alias="studentId")
    status: str = "draft"
    comment_text: str = Field(alias="commentText")
    locale: str = "en"
    disclaimer: str

    model_config = {"populate_by_name": True}
