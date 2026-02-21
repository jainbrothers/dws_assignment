from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.kafka import producer as kafka_producer_module

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    db_status = "ok"
    kafka_status = "ok"

    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"error: {exc}"

    if kafka_producer_module.get_producer() is None:
        kafka_status = "not_initialised"

    overall = "ok" if (db_status == "ok" and kafka_status == "ok") else "degraded"

    return {
        "status": overall,
        "db": db_status,
        "kafka": kafka_status,
    }
