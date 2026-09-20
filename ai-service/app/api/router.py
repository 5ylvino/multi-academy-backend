from fastapi import APIRouter

from app.api.health import router as health_router
from app.api.internal import router as internal_router
from app.api.v1.assistant import router as assistant_router
from app.api.v1.copilot import router as copilot_router
from app.api.v1.essay import router as essay_router
from app.api.v1.guidance import router as guidance_router
from app.api.v1.insights import router as insights_router
from app.api.v1.ingest import router as ingest_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.readiness import router as readiness_router
from app.api.v1.reports import router as reports_router
from app.api.v1.support import router as support_router
from app.api.v1.timetable import router as timetable_router
from app.api.v1.tutor import router as tutor_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(internal_router)
api_router.include_router(assistant_router)
api_router.include_router(support_router)
api_router.include_router(essay_router)
api_router.include_router(guidance_router)
api_router.include_router(insights_router)
api_router.include_router(copilot_router)
api_router.include_router(tutor_router)
api_router.include_router(reports_router)
api_router.include_router(readiness_router)
api_router.include_router(jobs_router)
api_router.include_router(ingest_router)
api_router.include_router(timetable_router)
