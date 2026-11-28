"""All v1 routers in one place."""

from __future__ import annotations

from fastapi import APIRouter

from app.presentation.http.v1.academics import routes as academics
from app.presentation.http.v1.assessment import results_routes as results
from app.presentation.http.v1.assessment import routes as assessment
from app.presentation.http.v1.attendance import routes as attendance
from app.presentation.http.v1.audit import routes as audit
from app.presentation.http.v1.communication import routes as communication
from app.presentation.http.v1.finance import online_routes, spending_routes
from app.presentation.http.v1.finance import routes as finance
from app.presentation.http.v1.identity import guardian_routes, mfa_routes, users
from app.presentation.http.v1.identity import routes as identity
from app.presentation.http.v1.inventory import routes as inventory
from app.presentation.http.v1.operations import (
    clinic_routes,
    gate_routes,
    hostel_routes,
    transport_routes,
)
from app.presentation.http.v1.payroll import routes as payroll
from app.presentation.http.v1.platform import routes as platform
from app.presentation.http.v1.safeguarding import routes as safeguarding
from app.presentation.http.v1.subscriptions import routes as subscriptions
from app.presentation.http.v1.tenancy import routes as tenancy

api_router = APIRouter()
api_router.include_router(identity.school_router)
api_router.include_router(identity.platform_router)
api_router.include_router(mfa_routes.router)
api_router.include_router(tenancy.school_router)
api_router.include_router(tenancy.platform_router)
api_router.include_router(platform.router)
api_router.include_router(users.router)
api_router.include_router(guardian_routes.router)
api_router.include_router(academics.router)
api_router.include_router(assessment.router)
api_router.include_router(results.router)
api_router.include_router(attendance.router)
api_router.include_router(finance.router)
api_router.include_router(audit.school_router)
api_router.include_router(audit.platform_router)
api_router.include_router(online_routes.router)
api_router.include_router(online_routes.webhook_router)
api_router.include_router(spending_routes.router)
api_router.include_router(inventory.router)
api_router.include_router(payroll.router)
api_router.include_router(communication.router)
api_router.include_router(safeguarding.router)
api_router.include_router(clinic_routes.router)
api_router.include_router(gate_routes.router)
api_router.include_router(transport_routes.router)
api_router.include_router(hostel_routes.router)
api_router.include_router(subscriptions.school_router)
api_router.include_router(subscriptions.public_router)
api_router.include_router(subscriptions.platform_router)
api_router.include_router(subscriptions.webhook_router)
