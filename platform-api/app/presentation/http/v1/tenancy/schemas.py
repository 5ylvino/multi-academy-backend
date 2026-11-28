"""Tenancy request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import EmailStr, Field

from app.presentation.http.v1.schemas import ApiModel, PageMeta

SchoolLevelName = Literal["nursery", "primary", "secondary"]


class SectionPlanRequest(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    levels: list[SchoolLevelName] = Field(min_length=1, max_length=3)


class RegisterSchoolRequest(ApiModel):
    school_name: str = Field(min_length=2, max_length=160)
    slug: str | None = Field(default=None, min_length=3, max_length=63)
    school_levels: list[SchoolLevelName] = Field(min_length=1, max_length=3)
    nursery_with_primary: bool = Field(
        default=True,
        description="With both nursery and primary: one section under the head teacher (true), "
        "or nursery on its own with its own head (false).",
    )
    sections: list[SectionPlanRequest] | None = Field(
        default=None,
        max_length=3,
        description="An explicit arrangement of levels into sections; overrides "
        "nursery_with_primary. Every chosen level must be in exactly one section.",
    )
    per_section_roles: list[
        Literal["bursar", "nurse", "guard", "driver", "administrative_staff"]
    ] = Field(
        default_factory=list,
        max_length=5,
        description="Service roles that work per section. Those not listed serve the whole school.",
    )
    country_code: str = Field(default="NG", min_length=2, max_length=2)
    timezone: str | None = Field(default=None, max_length=64, examples=["Africa/Lagos"])
    admin_full_name: str = Field(min_length=2, max_length=160)
    admin_email: EmailStr
    admin_password: str = Field(min_length=10, max_length=128)


class RegisterSchoolResponse(ApiModel):
    tenant_id: UUID
    slug: str
    status: str
    verification_expires_in: int


class VerifySchoolRequest(ApiModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")


class ResendVerificationRequest(ApiModel):
    email: EmailStr


class BootstrapSectionResponse(ApiModel):
    id: UUID
    name: str
    levels: list[str]
    features: list[str]


class BootstrapResponse(ApiModel):
    tenant_id: UUID
    school_name: str
    slug: str
    status: str
    plan_code: str
    school_levels: list[str]
    timezone: str
    config_version: int
    features: dict[str, bool]
    user_id: UUID
    roles: list[str]
    permissions: list[str]
    sections: list[BootstrapSectionResponse] = Field(
        description="The school's sections, each with its levels and the features it uses."
    )
    whole_school: bool = Field(description="True when this person covers every section.")
    my_section_ids: list[UUID] = Field(description="The sections to show this person.")


class TenantResponse(ApiModel):
    id: UUID
    slug: str
    name: str
    status: str
    status_reason: str | None
    plan_code: str
    school_levels: list[str]
    country_code: str
    contact_email: str
    created_at: datetime
    trial_ends_at: datetime | None
    config_version: int


class TenantPage(ApiModel):
    items: list[TenantResponse]
    page: PageMeta


class ChangeTenantStatusRequest(ApiModel):
    action: Literal["suspend", "reinstate", "activate"]
    reason: str | None = Field(default=None, max_length=500)
