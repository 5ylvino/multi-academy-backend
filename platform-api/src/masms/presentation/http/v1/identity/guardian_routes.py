"""Parent-to-student links."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Response, status

from masms.presentation.http.dependencies.auth import SchoolPrincipal
from masms.presentation.http.dependencies.context import UseCasesDep
from masms.presentation.http.v1.schemas import ApiModel

router = APIRouter(tags=["school users"])


class LinkGuardianRequest(ApiModel):
    parent_id: UUID
    relationship: Literal["mother", "father", "guardian", "sponsor", "other"] = "guardian"


class GuardianResponse(ApiModel):
    parent_id: UUID
    student_id: UUID
    parent_name: str
    student_name: str
    relationship: str
    created_at: datetime


@router.get("/users/{student_id}/guardians", response_model=list[GuardianResponse])
async def list_guardians(
    student_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> list[GuardianResponse]:
    links = await uc.guardians.guardians_of(principal, student_id)
    return [GuardianResponse.model_validate(g) for g in links]


@router.post(
    "/users/{student_id}/guardians",
    response_model=GuardianResponse,
    status_code=status.HTTP_201_CREATED,
)
async def link_guardian(
    student_id: UUID, body: LinkGuardianRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> GuardianResponse:
    view = await uc.guardians.link(
        principal, student_id=student_id, parent_id=body.parent_id, relationship=body.relationship
    )
    return GuardianResponse.model_validate(view)


@router.delete("/users/{student_id}/guardians/{parent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_guardian(
    student_id: UUID, parent_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep
) -> Response:
    await uc.guardians.unlink(principal, student_id=student_id, parent_id=parent_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me/children", response_model=list[GuardianResponse])
async def my_children(principal: SchoolPrincipal, uc: UseCasesDep) -> list[GuardianResponse]:
    return [GuardianResponse.model_validate(g) for g in await uc.guardians.my_children(principal)]
