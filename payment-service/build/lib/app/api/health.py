from fastapi import APIRouter

from app.db.session import ping_database

router = APIRouter(tags=["health"])


@router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def ready() -> dict[str, str]:
    if not ping_database():
        return {"status": "degraded", "database": "unreachable"}
    return {"status": "ok", "database": "reachable"}
