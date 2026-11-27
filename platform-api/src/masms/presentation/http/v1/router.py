"""All v1 routers in one place."""

from __future__ import annotations

from fastapi import APIRouter

from masms.presentation.http.v1.identity import routes as identity
from masms.presentation.http.v1.platform import routes as platform
from masms.presentation.http.v1.tenancy import routes as tenancy

api_router = APIRouter()
api_router.include_router(identity.school_router)
api_router.include_router(identity.platform_router)
api_router.include_router(tenancy.school_router)
api_router.include_router(tenancy.platform_router)
api_router.include_router(platform.router)
