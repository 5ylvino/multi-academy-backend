from fastapi import FastAPI

from app.api.router import api_router
from app.config import get_settings

app = FastAPI(
    title=get_settings().app_name,
    version="0.1.0",
    description="Async notifications and report generation workers.",
)
app.include_router(api_router)


@app.get("/health")
def health():
    return {"ok": True, "service": "worker"}
