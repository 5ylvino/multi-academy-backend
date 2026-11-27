"""School staff and student account models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field

from masms.presentation.http.v1.schemas import ApiModel, PageMeta


class InviteUserRequest(ApiModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=160)
    roles: list[str] = Field(min_length=1, max_length=8)
    capabilities: list[str] = Field(default_factory=list, max_length=64)


class AcceptInviteRequest(ApiModel):
    token: str = Field(min_length=16, max_length=256)
    password: str = Field(min_length=10, max_length=128)


class SchoolUserResponse(ApiModel):
    id: UUID
    email: str
    full_name: str
    roles: list[str]
    capabilities: list[str]
    status: str
    last_login_at: datetime | None
    created_at: datetime


class SchoolUserPage(ApiModel):
    items: list[SchoolUserResponse]
    page: PageMeta
