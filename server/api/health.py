from __future__ import annotations

import time

from fastapi import APIRouter

from server import catalog
from server.database import get_db
from server.models import HealthResponse

router = APIRouter()
_start_time: float = 0.0


def set_start_time() -> None:
    global _start_time
    _start_time = time.time()


@router.get("/health", response_model=HealthResponse)
async def health():
    db_status = "ok"
    try:
        async with get_db() as conn:
            await conn.execute("SELECT 1")
    except Exception:
        db_status = "error"

    return HealthResponse(
        status="healthy" if db_status == "ok" else "degraded",
        db=db_status,
        catalog_loaded=catalog.is_loaded(),
        catalog_products=catalog.product_count(),
        uptime_seconds=int(time.time() - _start_time),
    )
