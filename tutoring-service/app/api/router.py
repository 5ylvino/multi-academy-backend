from fastapi import APIRouter

from app.api.health import router as health_router
from app.api.v1.bookings import router as bookings_router
from app.api.v1.tutors import router as tutors_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(tutors_router)
api_router.include_router(bookings_router)
