"""Sections of a school: which levels each academic head leads, and who works where."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import Field

from app.presentation.http.dependencies.auth import SchoolPrincipal
from app.presentation.http.dependencies.context import UseCasesDep
from app.presentation.http.v1.schemas import ApiModel
from app.use_cases.academics.sections import SectionPlan

router = APIRouter(tags=["academics"])

Level = Literal["nursery", "primary", "secondary"]


class SectionHeadResponse(ApiModel):
    user_id: UUID
    full_name: str
    roles: list[str]


class SectionResponse(ApiModel):
    id: UUID
    name: str
    code: str
    levels: list[str] = Field(description="Youngest first.")
    heads: list[SectionHeadResponse]
    staff_count: int
    features: list[str] = Field(description="Features switched on for this section.")


class MySectionsResponse(ApiModel):
    whole_school: bool = Field(description="True for leaders who see every section.")
    sections: list[SectionResponse] = Field(description="The sections this person works in.")


class SectionPlanModel(ApiModel):
    id: UUID | None = Field(default=None, description="Keep an existing section; omit to add one.")
    name: str | None = Field(default=None, min_length=1, max_length=60)
    levels: list[Level] = Field(min_length=1, max_length=3)


class ArrangeSectionsRequest(ApiModel):
    sections: list[SectionPlanModel] = Field(min_length=1, max_length=3)


class StaffSectionsRequest(ApiModel):
    section_ids: list[UUID] = Field(max_length=3)


@router.get("/academics/sections", response_model=list[SectionResponse])
async def list_sections(principal: SchoolPrincipal, uc: UseCasesDep) -> list[SectionResponse]:
    return [SectionResponse.model_validate(v) for v in await uc.sections.list_all(principal)]


@router.get("/academics/sections/mine", response_model=MySectionsResponse)
async def my_sections(principal: SchoolPrincipal, uc: UseCasesDep) -> MySectionsResponse:
    whole, views = await uc.sections.mine(principal)
    return MySectionsResponse(
        whole_school=whole, sections=[SectionResponse.model_validate(v) for v in views]
    )


@router.put("/academics/sections", response_model=list[SectionResponse])
async def arrange_sections(
    body: ArrangeSectionsRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> list[SectionResponse]:
    """Re-group levels into sections: split nursery off primary, merge them, or rename.

    Classes, subjects and students follow their level. Staff of a removed section move to the
    sections that now hold its levels.
    """
    views = await uc.sections.arrange(
        principal, [SectionPlan(id=s.id, name=s.name, levels=list(s.levels)) for s in body.sections]
    )
    return [SectionResponse.model_validate(v) for v in views]


@router.put("/users/{user_id}/sections", response_model=list[SectionResponse])
async def set_staff_sections(
    user_id: UUID, body: StaffSectionsRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> list[SectionResponse]:
    """The sections a head, teacher or section staff member works in."""
    views = await uc.sections.assign(principal, user_id, body.section_ids)
    return [SectionResponse.model_validate(v) for v in views]


ServiceRole = Literal["bursar", "nurse", "guard", "driver", "administrative_staff"]


class StaffCoverageModel(ApiModel):
    per_section_roles: list[ServiceRole] = Field(
        max_length=5,
        description="Service roles that work per section; the others serve the whole school.",
    )


@router.get("/academics/sections/staff-coverage", response_model=StaffCoverageModel)
async def staff_coverage(principal: SchoolPrincipal, uc: UseCasesDep) -> StaffCoverageModel:
    roles = await uc.sections.staff_coverage(principal)
    return StaffCoverageModel.model_validate({"per_section_roles": roles})


@router.put("/academics/sections/staff-coverage", response_model=StaffCoverageModel)
async def set_staff_coverage(
    body: StaffCoverageModel, principal: SchoolPrincipal, uc: UseCasesDep
) -> StaffCoverageModel:
    roles = await uc.sections.set_staff_coverage(principal, list(body.per_section_roles))
    return StaffCoverageModel.model_validate({"per_section_roles": roles})


Feature = Literal[
    "attendance", "gradebook", "report_cards", "clinic", "gate", "transport", "boarding"
]


class SectionFeaturesRequest(ApiModel):
    features: list[Feature] = Field(max_length=16, description="The features to switch on.")


@router.put("/academics/sections/{section_id}/features", response_model=list[SectionResponse])
async def set_section_features(
    section_id: UUID, body: SectionFeaturesRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> list[SectionResponse]:
    """Each section runs its own activities: switch features on or off for one section."""
    views = await uc.sections.set_features(principal, section_id, list(body.features))
    return [SectionResponse.model_validate(v) for v in views]
