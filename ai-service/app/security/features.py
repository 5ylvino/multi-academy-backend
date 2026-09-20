from collections.abc import Callable

from fastapi import Depends, HTTPException, status

from app.security.context import RequestContext
from app.security.dependencies import get_request_context


def require_feature(feature: str) -> Callable:
    async def _dependency(ctx: RequestContext = Depends(get_request_context)) -> RequestContext:
        if feature not in ctx.features:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Feature {feature} is not enabled for this request",
            )
        return ctx

    return _dependency
