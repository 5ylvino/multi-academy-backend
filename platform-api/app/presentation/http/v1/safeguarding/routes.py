"""Safeguarding: staff record concerns; safeguarding leads work the cases."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from pydantic import Field

from app.presentation.http.dependencies.auth import SchoolPrincipal
from app.presentation.http.dependencies.context import UseCasesDep
from app.presentation.http.v1.schemas import ApiModel, PageMeta
from app.use_cases.safeguarding.cases import ReportCommand

router = APIRouter(prefix="/safeguarding", tags=["safeguarding"])

Cursor = Annotated[str | None, Query(max_length=64)]
Limit = Annotated[int, Query(ge=1, le=100)]
CATEGORIES = "physical|emotional|sexual|neglect|bullying|online|self_harm|domestic|attendance|other"
_PRIVATE = {"Cache-Control": "no-store"}


class ReportRequest(ApiModel):
    student_id: UUID
    category: str = Field(pattern=f"^({CATEGORIES})$")
    severity: str = Field(
        pattern="^(low|medium|high|urgent)$",
        description="urgent: the child may be at immediate risk; leads are emailed at once.",
    )
    account: str = Field(
        min_length=1,
        max_length=20_000,
        description="What you saw or heard, in the child's own words where possible.",
    )
    observed_at: datetime
    action_taken: str | None = Field(default=None, max_length=5_000)


class ConcernResponse(ApiModel):
    id: UUID
    student_id: UUID
    student_name: str
    category: str
    severity: str
    status: str
    observed_at: datetime
    reported_by: UUID
    reported_by_name: str
    reported_at: datetime
    lead_id: UUID | None
    lead_name: str | None
    closed_at: datetime | None


class MyReportResponse(ApiModel):
    concern: ConcernResponse
    account: str


class MyReportPage(ApiModel):
    items: list[MyReportResponse]
    page: PageMeta


class ConcernPage(ApiModel):
    items: list[ConcernResponse]
    page: PageMeta


class EntryResponse(ApiModel):
    id: UUID
    kind: str = Field(description="note, parent_contact, referral, status or assigned")
    text: str
    by: UUID
    by_name: str
    at: datetime
    agency: str | None
    agency_reference: str | None
    status: str | None


class CaseResponse(ApiModel):
    concern: ConcernResponse
    account: str
    chronology: list[EntryResponse] = Field(description="Oldest first.")


class NoteRequest(ApiModel):
    text: str = Field(min_length=1, max_length=20_000)
    parent_contact: bool = Field(default=False, description="Record contact with parents.")


class ReferRequest(ApiModel):
    agency: str = Field(min_length=2, max_length=120, description="e.g. police, social services")
    reference: str | None = Field(default=None, max_length=80)
    text: str = Field(min_length=1, max_length=20_000)


class AssignRequest(ApiModel):
    lead_id: UUID


class StatusRequest(ApiModel):
    status: str = Field(pattern="^(open|monitoring|referred|closed)$")
    reason: str = Field(min_length=1, max_length=5_000)


class CountsResponse(ApiModel):
    open: int
    monitoring: int
    referred: int
    closed: int
    urgent_open: int


@router.post("/concerns", response_model=MyReportResponse, status_code=status.HTTP_201_CREATED)
async def report(
    body: ReportRequest, principal: SchoolPrincipal, uc: UseCasesDep, response: Response
) -> MyReportResponse:
    """Any member of staff. Only safeguarding leads will see it."""
    response.headers.update(_PRIVATE)
    view = await uc.safeguarding.report(
        principal,
        ReportCommand(
            student_id=body.student_id,
            category=body.category,
            severity=body.severity,
            account=body.account,
            observed_at=body.observed_at,
            action_taken=body.action_taken,
        ),
    )
    return MyReportResponse.model_validate(view)


@router.get("/my-reports", response_model=MyReportPage)
async def my_reports(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    response: Response,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> MyReportPage:
    """What you reported and where each case stands."""
    response.headers.update(_PRIVATE)
    page = await uc.safeguarding.my_reports(principal, cursor=cursor, limit=limit)
    return MyReportPage(
        items=[MyReportResponse.model_validate(r) for r in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.get("/concerns", response_model=ConcernPage)
async def cases(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    response: Response,
    status_filter: Annotated[
        str | None, Query(alias="status", pattern="^(open|monitoring|referred|closed)$")
    ] = None,
    student_id: UUID | None = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> ConcernPage:
    """Safeguarding leads. Newest first; filter by status or student for a child's history."""
    response.headers.update(_PRIVATE)
    page = await uc.safeguarding.cases(
        principal, status=status_filter, student_id=student_id, cursor=cursor, limit=limit
    )
    return ConcernPage(
        items=[ConcernResponse.model_validate(c) for c in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.get("/counts", response_model=CountsResponse)
async def counts(principal: SchoolPrincipal, uc: UseCasesDep) -> CountsResponse:
    return CountsResponse.model_validate(await uc.safeguarding.counts(principal))


@router.get("/concerns/{concern_id}", response_model=CaseResponse)
async def open_case(
    concern_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep, response: Response
) -> CaseResponse:
    """The full case and chronology. Each opening is recorded."""
    response.headers.update(_PRIVATE)
    return CaseResponse.model_validate(await uc.safeguarding.open(principal, concern_id))


@router.post("/concerns/{concern_id}/notes", response_model=CaseResponse)
async def add_note(
    concern_id: UUID,
    body: NoteRequest,
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    response: Response,
) -> CaseResponse:
    response.headers.update(_PRIVATE)
    view = await uc.safeguarding.add_note(
        principal, concern_id, text=body.text, parent_contact=body.parent_contact
    )
    return CaseResponse.model_validate(view)


@router.post("/concerns/{concern_id}/referrals", response_model=CaseResponse)
async def refer(
    concern_id: UUID,
    body: ReferRequest,
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    response: Response,
) -> CaseResponse:
    response.headers.update(_PRIVATE)
    view = await uc.safeguarding.refer(
        principal, concern_id, agency=body.agency, reference=body.reference, text=body.text
    )
    return CaseResponse.model_validate(view)


@router.post("/concerns/{concern_id}/lead", response_model=CaseResponse)
async def assign(
    concern_id: UUID,
    body: AssignRequest,
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    response: Response,
) -> CaseResponse:
    response.headers.update(_PRIVATE)
    view = await uc.safeguarding.assign(principal, concern_id, lead_id=body.lead_id)
    return CaseResponse.model_validate(view)


@router.post("/concerns/{concern_id}/status", response_model=CaseResponse)
async def set_status(
    concern_id: UUID,
    body: StatusRequest,
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    response: Response,
) -> CaseResponse:
    """Monitor, close (with the outcome) or reopen."""
    response.headers.update(_PRIVATE)
    view = await uc.safeguarding.set_status(
        principal, concern_id, status=body.status, reason=body.reason
    )
    return CaseResponse.model_validate(view)
