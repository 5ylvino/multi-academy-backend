"""Identity request and response models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field

from app.presentation.http.v1.schemas import ApiModel
from app.use_cases.identity.session_issuer import IssuedSession


class SchoolSignInRequest(ApiModel):
    school: str = Field(min_length=3, max_length=63, description="The school's link name (slug).")
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class PlatformSignInRequest(ApiModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(ApiModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class SessionResponse(ApiModel):
    token_type: str = "Bearer"  # noqa: S105 - OAuth token type, not a secret
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime
    user_id: UUID
    tenant_id: UUID | None
    realm: str
    roles: list[str]

    @classmethod
    def of(cls, issued: IssuedSession) -> SessionResponse:
        return cls(
            access_token=issued.access_token,
            expires_in=issued.access_expires_in,
            refresh_token=issued.refresh_token,
            refresh_expires_at=issued.refresh_expires_at,
            user_id=issued.user_id,
            tenant_id=issued.tenant_id,
            realm=issued.realm,
            roles=issued.roles,
        )


class CurrentUserResponse(ApiModel):
    id: UUID
    realm: str
    tenant_id: UUID | None
    email: str
    full_name: str
    roles: list[str]
    permissions: list[str]
    primary_owner: bool
    last_login_at: datetime | None
