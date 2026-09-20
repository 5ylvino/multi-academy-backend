from fastapi import APIRouter, Depends, HTTPException, Query

from app.schemas.tutoring import TutorRegisterRequest, TutorUpdateRequest
from app.security.context import RequestContext
from app.security.dependencies import require_feature
from app.security.roles import is_teacher_role
from app.services.control_client import control_client
from app.services.tutor_service import TutorService

router = APIRouter(prefix="/v1/tutors", tags=["tutors"])


@router.post("/register")
async def register_tutor(
    payload: TutorRegisterRequest,
    ctx: RequestContext = Depends(require_feature("tutoring.marketplace")),
) -> dict:
    if not is_teacher_role(ctx.roles) and not payload.is_external:
        raise HTTPException(status_code=403, detail="Teacher role required for school tutors")
    result = TutorService().register(ctx, payload)
    return result.model_dump(by_alias=True)


@router.get("/search")
async def search_tutors(
    subject: str | None = Query(default=None),
    include_external: bool | None = Query(default=None, alias="includeExternal"),
    ctx: RequestContext = Depends(require_feature("tutoring.marketplace")),
) -> dict:
    policy = await control_client.get_tutoring_policy(ctx.tenant_id)
    if not policy.get("marketplaceEnabled", True):
        return {"items": [], "total": 0}
    resolved_include_external = (
        include_external
        if include_external is not None
        else bool(policy.get("allowExternalTutors", True))
    )
    result = TutorService().search(
        ctx,
        subject=subject,
        include_external=resolved_include_external,
    )
    return result.model_dump(by_alias=True)


@router.get("/me")
async def my_tutor_profile(
    ctx: RequestContext = Depends(require_feature("tutoring.marketplace")),
) -> dict:
    result = TutorService().get_mine(ctx)
    if result is None:
        raise HTTPException(status_code=404, detail="Tutor profile not found")
    return result.model_dump(by_alias=True)


@router.patch("/{tutor_id}")
async def update_tutor(
    tutor_id: str,
    payload: TutorUpdateRequest,
    ctx: RequestContext = Depends(require_feature("tutoring.marketplace")),
) -> dict:
    try:
        result = TutorService().update(ctx, tutor_id, payload)
        return result.model_dump(by_alias=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
