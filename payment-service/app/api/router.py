from fastapi import APIRouter

from app.api.health import router as health_router
from app.api.v1.checkout import router as checkout_router
from app.api.v1.contexts import router as contexts_router
from app.api.v1.webhooks import router as webhooks_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(checkout_router)
api_router.include_router(contexts_router)
api_router.include_router(webhooks_router)
