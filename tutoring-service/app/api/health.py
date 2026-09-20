from fastapi import APIRouter, Response

from app.db.session import ping_database

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(response: Response) -> dict:
    db_ok = ping_database()
    if not db_ok:
        response.status_code = 503
    return {"status": "ok" if db_ok else "degraded", "database": db_ok}
