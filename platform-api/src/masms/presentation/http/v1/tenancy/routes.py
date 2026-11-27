"""School sign-up and verification, school bootstrap, and owner-console school management."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from masms.presentation.http.dependencies.auth import PlatformPrincipal, SchoolPrincipal
from masms.presentation.http.dependencies.context import ClientInfoDep, UseCasesDep
from masms.presentation.http.v1.identity.schemas import SessionResponse
from masms.presentation.http.v1.schemas import PageMeta
from masms.presentation.http.v1.tenancy.schemas import (
    BootstrapResponse,
    ChangeTenantStatusRequest,
    RegisterSchoolRequest,
    RegisterSchoolResponse,
    ResendVerificationRequest,
    TenantPage,
    TenantResponse,
    VerifySchoolRequest,
)
from masms.use_cases.tenancy.manage_tenants import ChangeTenantStatusCommand
from masms.use_cases.tenancy.register_school import RegisterSchoolCommand
from masms.use_cases.tenancy.verify_school_email import (
    ResendVerificationCommand,
    VerifySchoolEmailCommand,
)

school_router = APIRouter(tags=["schools"])
platform_router = APIRouter(prefix="/platform/tenants", tags=["platform schools"])


@school_router.post(
    "/schools", response_model=RegisterSchoolResponse, status_code=status.HTTP_201_CREATED
)
async def register_school(
    body: RegisterSchoolRequest, uc: UseCasesDep, client: ClientInfoDep
) -> RegisterSchoolResponse:
    result = await uc.register_school(
        RegisterSchoolCommand(
            school_name=body.school_name,
            slug=body.slug,
            school_levels=list(body.school_levels),
            country_code=body.country_code,
            timezone=body.timezone,
            admin_full_name=body.admin_full_name,
            admin_email=body.admin_email,
            admin_password=body.admin_password,
            client_ip=client.ip_address,
        )
    )
    return RegisterSchoolResponse.model_validate(result)


@school_router.post("/schools/{slug}/verify", response_model=SessionResponse)
async def verify_school(
    slug: str, body: VerifySchoolRequest, uc: UseCasesDep, client: ClientInfoDep
) -> SessionResponse:
    issued = await uc.verify_school_email(
        VerifySchoolEmailCommand(slug=slug, email=body.email, code=body.code, client=client)
    )
    return SessionResponse.of(issued)


@school_router.post("/schools/{slug}/verification/resend", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(
    slug: str, body: ResendVerificationRequest, uc: UseCasesDep, client: ClientInfoDep
) -> dict[str, str]:
    await uc.resend_verification(
        ResendVerificationCommand(slug=slug, email=body.email, client_ip=client.ip_address)
    )
    return {"status": "accepted"}


@school_router.get("/bootstrap", response_model=BootstrapResponse)
async def bootstrap(
    principal: SchoolPrincipal, uc: UseCasesDep, request: Request
) -> BootstrapResponse:
    return BootstrapResponse.model_validate(await uc.school_bootstrap(principal))


@platform_router.get("", response_model=TenantPage)
async def list_tenants(
    principal: PlatformPrincipal,
    uc: UseCasesDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
) -> TenantPage:
    page = await uc.list_tenants(
        principal, status=status_filter, search=q, cursor=cursor, limit=limit
    )
    return TenantPage(
        items=[TenantResponse.model_validate(t) for t in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@platform_router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: UUID, principal: PlatformPrincipal, uc: UseCasesDep
) -> TenantResponse:
    return TenantResponse.model_validate(await uc.get_tenant(principal, tenant_id))


@platform_router.post("/{tenant_id}/status", response_model=TenantResponse)
async def change_status(
    tenant_id: UUID, body: ChangeTenantStatusRequest, principal: PlatformPrincipal, uc: UseCasesDep
) -> TenantResponse:
    view = await uc.change_tenant_status(
        principal,
        ChangeTenantStatusCommand(tenant_id=tenant_id, action=body.action, reason=body.reason),
    )
    return TenantResponse.model_validate(view)
