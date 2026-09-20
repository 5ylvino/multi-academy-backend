from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.security.context import RequestContext
from app.security.roles import is_admin_role
from app.security.service_jwt import ServiceJwtError, build_request_context, decode_service_token

_bearer = HTTPBearer(auto_error=False)


async def get_request_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> RequestContext:
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    try:
        payload = decode_service_token(credentials.credentials)
        return build_request_context(payload, header_tenant_id=x_tenant_id, request_id=x_request_id)
    except ServiceJwtError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


def require_feature(feature: str):
    async def _dep(ctx: RequestContext = Depends(get_request_context)) -> RequestContext:
        if feature not in ctx.features:
            raise HTTPException(status_code=403, detail=f"Feature {feature} not enabled")
        return ctx

    return _dep


def require_admin():
    async def _dep(ctx: RequestContext = Depends(get_request_context)) -> RequestContext:
        if not is_admin_role(ctx.roles):
            raise HTTPException(status_code=403, detail="Admin role required")
        return ctx

    return _dep
