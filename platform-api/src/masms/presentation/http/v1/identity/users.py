"""School user management: invite staff or students, list accounts, accept an invite."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from masms.presentation.http.dependencies.auth import SchoolPrincipal
from masms.presentation.http.dependencies.context import ClientInfoDep, UseCasesDep
from masms.presentation.http.v1.identity.schemas import SessionResponse
from masms.presentation.http.v1.identity.users_schemas import (
    AcceptInviteRequest,
    InviteUserRequest,
    SchoolUserPage,
    SchoolUserResponse,
)
from masms.presentation.http.v1.schemas import PageMeta
from masms.use_cases.identity.manage_school_users import (
    AcceptInviteCommand,
    InviteSchoolUserCommand,
)

router = APIRouter(tags=["school users"])


@router.post("/users", response_model=SchoolUserResponse, status_code=status.HTTP_201_CREATED)
async def invite_user(
    body: InviteUserRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> SchoolUserResponse:
    view = await uc.invite_school_user(
        principal,
        InviteSchoolUserCommand(
            email=body.email,
            full_name=body.full_name,
            roles=body.roles,
            capabilities=body.capabilities,
        ),
    )
    return SchoolUserResponse.model_validate(view)


@router.get("/users", response_model=SchoolUserPage)
async def list_users(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    role: Annotated[str | None, Query(max_length=40)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
) -> SchoolUserPage:
    page = await uc.list_school_users(principal, role=role, search=q, cursor=cursor, limit=limit)
    return SchoolUserPage(
        items=[SchoolUserResponse.model_validate(u) for u in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.post("/schools/{slug}/invites/accept", response_model=SessionResponse)
async def accept_invite(
    slug: str, body: AcceptInviteRequest, uc: UseCasesDep, client: ClientInfoDep
) -> SessionResponse:
    issued = await uc.accept_invite(
        AcceptInviteCommand(
            school_slug=slug, token=body.token, password=body.password, client=client
        )
    )
    return SessionResponse.of(issued)
