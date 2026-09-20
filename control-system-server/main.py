"""Entry point: uvicorn main:app --reload --port 8000"""

from app.main import app

__all__ = ["app"]
