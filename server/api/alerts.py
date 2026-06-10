from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from server import catalog
from server.api.favorites import PRODUCT_ID_RE
from server.auth.jwt import get_current_user
from server.config import ALERTS_MAX
from server.database import get_db
from server.models import AlertRequest, AlertsResponse, ErrorResponse
from server.rate_limit import limiter

router = APIRouter(prefix="/api")


@router.get("/alerts", response_model=AlertsResponse, responses={401: {"model": ErrorResponse}})
@limiter.limit("60/minute")
async def get_alerts(request: Request, user: dict = Depends(get_current_user)):
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT product_id, target_price, triggered FROM price_alerts WHERE user_id = ? ORDER BY created_at",
            (user["sub"],),
        )
        rows = await cursor.fetchall()
    return AlertsResponse(
        alerts=[
            {
                "product_id": r["product_id"],
                "target_price": r["target_price"],
                "triggered": bool(r["triggered"]),
            }
            for r in rows
        ]
    )


@router.put(
    "/alerts/{product_id}",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
@limiter.limit("60/minute")
async def upsert_alert(
    product_id: str,
    payload: AlertRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    if not PRODUCT_ID_RE.fullmatch(product_id):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "not_found", "message": "유효하지 않은 제품 ID 형식입니다"},
        )

    if catalog.is_loaded() and catalog.product_count() > 0 and not catalog.is_valid_product(product_id):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "not_found", "message": f"존재하지 않는 제품입니다: {product_id}"},
        )

    async with get_db() as db:
        cursor = await db.execute(
            "SELECT 1 FROM price_alerts WHERE user_id = ? AND product_id = ?",
            (user["sub"], product_id),
        )
        exists = await cursor.fetchone() is not None

        if not exists:
            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM price_alerts WHERE user_id = ?",
                (user["sub"],),
            )
            count = (await cursor.fetchone())["cnt"]
            if count >= ALERTS_MAX:
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    content={"error": "limit_exceeded", "message": f"가격 알림은 최대 {ALERTS_MAX}개까지 가능합니다"},
                )

        # 목표가를 바꾸면 다시 무장 상태(triggered=0)로 돌아간다.
        await db.execute(
            """INSERT INTO price_alerts (user_id, product_id, target_price)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id, product_id) DO UPDATE SET
                 target_price = excluded.target_price,
                 triggered = 0,
                 updated_at = datetime('now')""",
            (user["sub"], product_id, payload.target_price),
        )
        await db.commit()
    return {"ok": True}


@router.delete("/alerts/{product_id}", responses={401: {"model": ErrorResponse}})
@limiter.limit("60/minute")
async def remove_alert(product_id: str, request: Request, user: dict = Depends(get_current_user)):
    async with get_db() as db:
        await db.execute(
            "DELETE FROM price_alerts WHERE user_id = ? AND product_id = ?",
            (user["sub"], product_id),
        )
        await db.commit()
    return {"ok": True}
