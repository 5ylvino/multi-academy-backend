"""Authentication and authorization dependencies for both realms."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.domain.identity.roles import Realm
from app.domain.shared.errors import AuthenticationFailed, TenantUnavailable
from app.infrastructure.container import Container
from app.presentation.http.dependencies.context import get_container
from app.use_cases.shared.principal import Principal

_bearer = HTTPBearer(auto_error=False, description="Access token from sign-in")


def _token(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise AuthenticationFailed("Sign in to continue.", code="not_authenticated")
    return credentials.credentials


async def school_principal(
    request: Request,
    container: Annotated[Container, Depends(get_container)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    principal = Principal.from_claims(container.decode_access(_token(credentials), Realm.SCHOOL))
    runtime = await container.use_cases.tenant_runtime(principal.school_tenant_id)
    if runtime is None or not runtime.serving:
        raise TenantUnavailable(
            "This school account is not available.",
            details={"status": runtime.status if runtime else "unknown"},
        )
    request.state.tenant_runtime = runtime
    return principal


async def platform_principal(
    container: Annotated[Container, Depends(get_container)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    return Principal.from_claims(container.decode_access(_token(credentials), Realm.PLATFORM))


SchoolPrincipal = Annotated[Principal, Depends(school_principal)]
PlatformPrincipal = Annotated[Principal, Depends(platform_principal)]


def require_school_permission(*permissions: str) -> Callable[..., Awaitable[Principal]]:
    async def dependency(principal: SchoolPrincipal) -> Principal:
        principal.require(*permissions)
        return principal

    return dependency


def require_feature(key: str) -> Callable[..., Awaitable[Principal]]:
    """Gate a school route on a feature flag. Fails closed when the flag is unknown."""

    async def dependency(
        request: Request,
        principal: SchoolPrincipal,
        container: Annotated[Container, Depends(get_container)],
    ) -> Principal:
        runtime = request.state.tenant_runtime
        await container.use_cases.tenant_features.require(
            principal.school_tenant_id,
            key,
            config_version=runtime.config_version,
            plan_code=runtime.plan_code,
        )
        return principal

    return dependency
