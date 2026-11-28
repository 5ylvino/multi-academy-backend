"""Results: the class broadsheet, report cards, comments, and release to families."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import ConfigDict, Field, model_validator

from app.presentation.http.dependencies.auth import SchoolPrincipal
from app.presentation.http.dependencies.context import UseCasesDep
from app.presentation.http.v1.schemas import ApiModel

router = APIRouter(prefix="/assessment", tags=["assessment"])

_DECIMAL = "An exact decimal, as a string."


class CardStudent(ApiModel):
    id: UUID
    name: str


class CardClass(ApiModel):
    id: UUID
    name: str
    school_level: str
    class_teacher: str | None


class CardTerm(ApiModel):
    id: UUID
    name: str
    code: str
    session_name: str
    starts_on: date
    ends_on: date


class CardComponent(ApiModel):
    code: str
    name: str
    max_score: int


class CardBand(ApiModel):
    grade: str
    remark: str
    min_score: int
    max_score: int


class SubjectLineResponse(ApiModel):
    subject_id: UUID
    subject_name: str
    parts: dict[str, str] = Field(description="Score per scheme part code. " + _DECIMAL)
    total: str = Field(description=_DECIMAL)
    earlier_totals: list[str] = Field(description="Released totals of earlier terms this session.")
    cumulative: str | None = Field(description="Average of this and earlier terms' totals.")
    grade: str
    remark: str
    position: int
    out_of: int
    class_average: str
    highest: str
    lowest: str


class CardAttendance(ApiModel):
    present: int
    late: int
    absent: int
    excused: int
    days: int


class CardComments(ApiModel):
    class_teacher: str | None
    head: str | None


class ReportCardResponse(ApiModel):
    student: CardStudent
    class_: CardClass = Field(alias="class")
    term: CardTerm
    components: list[CardComponent]
    subjects: list[SubjectLineResponse]
    total: str
    average: str | None
    grade: str | None
    remark: str | None
    position: int | None
    out_of: int
    class_average: str | None
    attendance: CardAttendance
    comments: CardComments
    next_term_starts_on: date | None
    scale: list[CardBand]
    released: bool = Field(description="False while staff preview marks before release.")
    version: int | None = None
    released_at: datetime | None = None

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        from_attributes=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )


class SubjectStatusResponse(ApiModel):
    subject_id: UUID
    subject_name: str
    status: str = Field(description="no_marks, draft, submitted or approved.")


class BroadsheetResponse(ApiModel):
    term_id: UUID
    class_id: UUID
    class_name: str
    release_status: str | None = Field(description="null, released or withdrawn.")
    release_version: int | None
    subjects: list[SubjectStatusResponse]
    ready_to_release: bool
    class_average: Decimal | None
    cards: list[ReportCardResponse] = Field(description="Best position first.")

    @model_validator(mode="before")
    @classmethod
    def _preview_cards(cls, data: Any) -> Any:
        cards = getattr(data, "cards", None)
        if cards is None:
            return data
        return {
            **{k: getattr(data, k) for k in data.__dataclass_fields__ if k != "cards"},
            "cards": [{**c, "released": False} for c in cards],
        }


class ReportSummaryResponse(ApiModel):
    term_id: UUID
    term_name: str
    session_name: str
    class_name: str
    average: str | None
    position: int | None
    out_of: int
    released_at: datetime


class CommentRequest(ApiModel):
    teacher_comment: str | None = Field(
        default=None, max_length=500, description="The class teacher's comment; null clears it."
    )
    head_comment: str | None = Field(
        default=None, max_length=500, description="The head's comment; null clears it."
    )


class WithdrawRequest(ApiModel):
    reason: str = Field(min_length=3, max_length=500)


@router.get("/terms/{term_id}/classes/{class_id}/broadsheet", response_model=BroadsheetResponse)
async def broadsheet(
    term_id: UUID, class_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> BroadsheetResponse:
    """Every student's results from the marks as they stand, with what still blocks release."""
    view = await uc.results.broadsheet(principal, term_id=term_id, class_id=class_id)
    return BroadsheetResponse.model_validate(view)


@router.post("/terms/{term_id}/classes/{class_id}/release", response_model=BroadsheetResponse)
async def release(
    term_id: UUID, class_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> BroadsheetResponse:
    """Publishes report cards to students and guardians. Every subject must be approved."""
    view = await uc.results.release(principal, term_id=term_id, class_id=class_id)
    return BroadsheetResponse.model_validate(view)


@router.post("/terms/{term_id}/classes/{class_id}/withdraw", response_model=BroadsheetResponse)
async def withdraw(
    term_id: UUID,
    class_id: UUID,
    body: WithdrawRequest,
    principal: SchoolPrincipal,
    uc: UseCasesDep,
) -> BroadsheetResponse:
    """Hides released report cards, so marks can be corrected and the results released again."""
    view = await uc.results.withdraw(
        principal, term_id=term_id, class_id=class_id, reason=body.reason
    )
    return BroadsheetResponse.model_validate(view)


@router.get("/students/{student_id}/report-cards", response_model=list[ReportSummaryResponse])
async def report_cards_of(
    student_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> list[ReportSummaryResponse]:
    """Released report cards, newest term first."""
    found = await uc.results.report_cards_of(principal, student_id)
    return [ReportSummaryResponse.model_validate(s) for s in found]


@router.get("/students/{student_id}/report-cards/{term_id}", response_model=ReportCardResponse)
async def report_card(
    student_id: UUID, term_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> ReportCardResponse:
    card = await uc.results.report_card(principal, term_id=term_id, student_id=student_id)
    return ReportCardResponse.model_validate(card)


@router.put("/students/{student_id}/report-cards/{term_id}/comments", status_code=204)
async def set_comment(
    student_id: UUID,
    term_id: UUID,
    body: CommentRequest,
    principal: SchoolPrincipal,
    uc: UseCasesDep,
) -> None:
    """Send only the comment you are writing: the class teacher's, the head's, or both."""
    fields = body.model_fields_set
    await uc.results.set_comment(
        principal,
        term_id=term_id,
        student_id=student_id,
        teacher_comment=body.teacher_comment,
        head_comment=body.head_comment,
        set_teacher="teacher_comment" in fields,
        set_head="head_comment" in fields,
    )
