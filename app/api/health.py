from fastapi import APIRouter
from sqlalchemy import text

from app.db.engine import engine

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict:
    """Readiness probe: verifies DB connectivity via RDS Proxy."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ready"}
