"""Assessment: the scoring scheme and grading scale, and the gradebook."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path
from pydantic import Field

from app.domain.assessment.scheme import Band, ComponentKind, ComponentSpec
from app.presentation.http.dependencies.auth import SchoolPrincipal
from app.presentation.http.dependencies.context import UseCasesDep
from app.presentation.http.v1.schemas import ApiModel
from app.use_cases.assessment.gradebook import Entry

router = APIRouter(prefix="/assessment", tags=["assessment"])

Level = Annotated[str, Path(pattern="^(nursery|primary|secondary)$")]
Score = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]


class ComponentRequest(ApiModel):
    code: str = Field(min_length=1, max_length=24, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=40)
    kind: str = Field(pattern="^(continuous|exam)$")
    max_score: int = Field(ge=1, le=100)


class SchemeRequest(ApiModel):
    components: list[ComponentRequest] = Field(
        min_length=1, max_length=8, description="In report-card order; must add up to 100."
    )


class ComponentResponse(ApiModel):
    id: UUID
    code: str
    name: str
    kind: str
    max_score: int


class SchemeResponse(ApiModel):
    school_level: str
    components: list[ComponentResponse]
    locked: bool = Field(description="Marks exist this term; the parts can change next term.")


class BandModel(ApiModel):
    grade: str = Field(min_length=1, max_length=3)
    remark: str = Field(min_length=1, max_length=40)
    min_score: int = Field(ge=0, le=100)
    max_score: int = Field(ge=0, le=100)


class ScaleRequest(ApiModel):
    bands: list[BandModel] = Field(
        min_length=2, max_length=12, description="Must cover 0 to 100 with no gaps or overlaps."
    )


class SheetScope(ApiModel):
    term_id: UUID
    class_id: UUID
    subject_id: UUID


class EntryRequest(ApiModel):
    student_id: UUID
    component_id: UUID
    score: Score | None = Field(description="null clears the mark.")


class MarksRequest(SheetScope):
    entries: list[EntryRequest] = Field(min_length=1, max_length=3_000)


class ExemptRequest(SheetScope):
    student_id: UUID
    exempt: bool = Field(description="true: the student doesn't take this subject.")


class NoteRequest(ApiModel):
    note: str = Field(min_length=1, max_length=500)


class StudentMarksResponse(ApiModel):
    student_id: UUID
    student_name: str
    exempt: bool
    marks: dict[UUID, float] = Field(description="By part id.")
    total: float | None = Field(description="Out of 100, once every part has a mark.")
    missing: int


class SheetResponse(ApiModel):
    id: UUID | None = Field(description="null until the first mark is saved.")
    term_id: UUID
    class_id: UUID
    class_name: str
    subject_id: UUID
    subject_name: str
    teacher_id: UUID | None
    teacher_name: str | None
    status: str = Field(description="draft, submitted or approved")
    note: str | None = Field(description="Why it was sent back or reopened.")
    submitted_by: UUID | None
    submitted_at: datetime | None
    approved_by: UUID | None
    approved_at: datetime | None
    components: list[ComponentResponse]
    students: list[StudentMarksResponse]
    missing: int
    can_edit: bool
    can_approve: bool


class SheetSummaryResponse(ApiModel):
    id: UUID | None
    class_id: UUID
    class_name: str
    subject_id: UUID
    subject_name: str
    teacher_id: UUID | None
    teacher_name: str | None
    status: str
    missing: int
    updated_at: datetime | None


@router.get("/schemes/{school_level}", response_model=SchemeResponse)
async def scheme(
    school_level: Level, principal: SchoolPrincipal, uc: UseCasesDep
) -> SchemeResponse:
    return SchemeResponse.model_validate(await uc.assessment_setup.scheme(principal, school_level))


@router.put("/schemes/{school_level}", response_model=SchemeResponse)
async def save_scheme(
    school_level: Level, body: SchemeRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> SchemeResponse:
    view = await uc.assessment_setup.save_scheme(
        principal,
        school_level,
        [
            ComponentSpec(
                code=c.code, name=c.name, kind=ComponentKind(c.kind), max_score=c.max_score
            )
            for c in body.components
        ],
    )
    return SchemeResponse.model_validate(view)


@router.get("/scales/{school_level}", response_model=list[BandModel])
async def scale(
    school_level: Level, principal: SchoolPrincipal, uc: UseCasesDep
) -> list[BandModel]:
    bands = await uc.assessment_setup.scale(principal, school_level)
    return [BandModel.model_validate(b) for b in bands]


@router.put("/scales/{school_level}", response_model=list[BandModel])
async def save_scale(
    school_level: Level, body: ScaleRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> list[BandModel]:
    bands = await uc.assessment_setup.save_scale(
        principal,
        school_level,
        [
            Band(grade=b.grade, remark=b.remark, min_score=b.min_score, max_score=b.max_score)
            for b in body.bands
        ],
    )
    return [BandModel.model_validate(b) for b in bands]


@router.get("/sheet", response_model=SheetResponse)
async def sheet(
    term_id: UUID, class_id: UUID, subject_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> SheetResponse:
    view = await uc.gradebook.sheet(
        principal, term_id=term_id, class_id=class_id, subject_id=subject_id
    )
    return SheetResponse.model_validate(view)


@router.put("/sheet/marks", response_model=SheetResponse)
async def save_marks(
    body: MarksRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> SheetResponse:
    """Saves any number of marks at once (autosave). Unchanged marks are skipped."""
    view = await uc.gradebook.save_marks(
        principal,
        term_id=body.term_id,
        class_id=body.class_id,
        subject_id=body.subject_id,
        entries=[
            Entry(student_id=e.student_id, component_id=e.component_id, score=e.score)
            for e in body.entries
        ],
    )
    return SheetResponse.model_validate(view)


@router.put("/sheet/exemptions", response_model=SheetResponse)
async def set_exempt(
    body: ExemptRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> SheetResponse:
    view = await uc.gradebook.set_exempt(
        principal,
        term_id=body.term_id,
        class_id=body.class_id,
        subject_id=body.subject_id,
        student_id=body.student_id,
        exempt=body.exempt,
    )
    return SheetResponse.model_validate(view)


@router.post("/sheets/{sheet_id}/submit", response_model=SheetResponse)
async def submit(sheet_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep) -> SheetResponse:
    """Every student needs every mark (or to be marked as not taking the subject)."""
    return SheetResponse.model_validate(await uc.gradebook.submit(principal, sheet_id))


@router.post("/sheets/{sheet_id}/recall", response_model=SheetResponse)
async def recall(sheet_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep) -> SheetResponse:
    return SheetResponse.model_validate(await uc.gradebook.recall(principal, sheet_id))


@router.post("/sheets/{sheet_id}/approve", response_model=SheetResponse)
async def approve(sheet_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep) -> SheetResponse:
    return SheetResponse.model_validate(await uc.gradebook.approve(principal, sheet_id))


@router.post("/sheets/{sheet_id}/send-back", response_model=SheetResponse)
async def send_back(
    sheet_id: UUID, body: NoteRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> SheetResponse:
    view = await uc.gradebook.send_back(principal, sheet_id, note=body.note)
    return SheetResponse.model_validate(view)


@router.post("/sheets/{sheet_id}/reopen", response_model=SheetResponse)
async def reopen(
    sheet_id: UUID, body: NoteRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> SheetResponse:
    view = await uc.gradebook.reopen(principal, sheet_id, note=body.note)
    return SheetResponse.model_validate(view)


@router.get("/terms/{term_id}/classes/{class_id}/sheets", response_model=list[SheetSummaryResponse])
async def class_overview(
    term_id: UUID, class_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> list[SheetSummaryResponse]:
    found = await uc.gradebook.class_overview(principal, term_id=term_id, class_id=class_id)
    return [SheetSummaryResponse.model_validate(s) for s in found]


@router.get("/terms/{term_id}/my-sheets", response_model=list[SheetSummaryResponse])
async def my_sheets(
    term_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> list[SheetSummaryResponse]:
    found = await uc.gradebook.my_sheets(principal, term_id=term_id)
    return [SheetSummaryResponse.model_validate(s) for s in found]


@router.get("/terms/{term_id}/awaiting-approval", response_model=list[SheetSummaryResponse])
async def awaiting_approval(
    term_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> list[SheetSummaryResponse]:
    found = await uc.gradebook.awaiting_approval(principal, term_id=term_id)
    return [SheetSummaryResponse.model_validate(s) for s in found]
