from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.security.context import RequestContext
from app.security.service_jwt import ServiceJwtError, build_request_context, decode_service_token

_bearer = HTTPBearer(auto_error=False)


async def get_request_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> RequestContext:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    try:
        payload = decode_service_token(credentials.credentials)
        return build_request_context(
            payload,
            header_tenant_id=x_tenant_id,
            request_id=x_request_id,
        )
    except ServiceJwtError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
