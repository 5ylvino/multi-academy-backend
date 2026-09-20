from fastapi import APIRouter, Depends, HTTPException, status

from app.security.context import RequestContext
from app.security.dependencies import get_request_context
from app.services.health import ping_llm

router = APIRouter(prefix="/v1/internal")


@router.post("/llm/ping")
async def llm_ping(_ctx: RequestContext = Depends(get_request_context)) -> dict:
    """Authenticated LLM smoke test — verifies NVIDIA/Kilo connectivity."""
    try:
        return await ping_llm()
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM ping failed: {exc}",
        ) from exc
