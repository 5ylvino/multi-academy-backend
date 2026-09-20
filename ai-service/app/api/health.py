from fastapi import APIRouter, Response, status

from app.services.health import build_ready_report

router = APIRouter()


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(response: Response) -> dict:
    report = await build_ready_report()
    if report.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report.to_dict()
