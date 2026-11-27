"""Tenancy request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import EmailStr, Field

from app.presentation.http.v1.schemas import ApiModel, PageMeta

SchoolLevelName = Literal["nursery", "primary", "secondary"]


class RegisterSchoolRequest(ApiModel):
    school_name: str = Field(min_length=2, max_length=160)
    slug: str | None = Field(default=None, min_length=3, max_length=63)
    school_levels: list[SchoolLevelName] = Field(min_length=1, max_length=3)
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
