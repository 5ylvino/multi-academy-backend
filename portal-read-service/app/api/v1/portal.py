from fastapi import APIRouter, Depends

from app.security.context import RequestContext
from app.security.dependencies import get_request_context, require_feature
from app.services.redis_cache import PortalCache
from app.services.school_client import SchoolClient

router = APIRouter(prefix="/v1", tags=["portal"])
_cache = PortalCache()
_school = SchoolClient()


async def _cached(ctx: RequestContext, path: str, feature: str):
    _ = feature
    cached = await _cache.get(ctx.tenant_id, path, ctx.actor_id)
    if cached is not None:
        return cached
    data = await _school.fetch_internal(ctx, path)
    await _cache.set(ctx.tenant_id, path, ctx.actor_id, data)
    return data


@router.get("/parent/home")
async def parent_home(ctx: RequestContext = Depends(require_feature("portal.parent"))):
    return await _cached(ctx, "parent/home", "portal.parent")


@router.get("/parent/wards/{student_id}/overview")
async def parent_ward_overview(
    student_id: str,
    ctx: RequestContext = Depends(require_feature("portal.parent")),
):
    return await _cached(ctx, f"parent/wards/{student_id}/overview", "portal.parent")


@router.get("/student/overview")
async def student_overview(ctx: RequestContext = Depends(require_feature("portal.student"))):
    return await _cached(ctx, "student/overview", "portal.student")


@router.get("/bursar/dashboard")
async def bursar_dashboard(ctx: RequestContext = Depends(require_feature("portal.bursar"))):
    return await _cached(ctx, "bursar/dashboard", "portal.bursar")


@router.get("/health")
async def health():
    return {"ok": True, "service": "portal-read"}
