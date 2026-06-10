from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from server import catalog
from server.auth.jwt import get_current_user
from server.config import FAVORITES_MAX
from server.database import get_db
from server.models import ErrorResponse, FavoritesResponse
from server.rate_limit import limiter

router = APIRouter(prefix="/api")

# 카탈로그가 아직 로드되지 않은(fail-open) 상태에서도 임의 문자열이 저장되지
# 않도록 제품 ID 슬러그 형식을 강제한다. tests/test_product_id_guard.py가
# config/products.yaml의 전체 ID가 이 패턴에 부합함을 보장한다.
PRODUCT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")


@router.get("/favorites", response_model=FavoritesResponse, responses={401: {"model": ErrorResponse}})
@limiter.limit("60/minute")
async def get_favorites(request: Request, user: dict = Depends(get_current_user)):
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT product_id FROM favorites WHERE user_id = ? ORDER BY added_at",
            (user["sub"],),
        )
        rows = await cursor.fetchall()
    return FavoritesResponse(favorites=[r["product_id"] for r in rows])


@router.put("/favorites/{product_id}", responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
@limiter.limit("60/minute")
async def add_favorite(product_id: str, request: Request, user: dict = Depends(get_current_user)):
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
            "SELECT 1 FROM favorites WHERE user_id = ? AND product_id = ?",
            (user["sub"], product_id),
        )
        if await cursor.fetchone():
            return {"ok": True}

        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM favorites WHERE user_id = ?",
            (user["sub"],),
        )
        count = (await cursor.fetchone())["cnt"]
        if count >= FAVORITES_MAX:
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content={"error": "limit_exceeded", "message": f"관심 목록은 최대 {FAVORITES_MAX}개까지 가능합니다"},
            )

        await db.execute(
            "INSERT INTO favorites (user_id, product_id) VALUES (?, ?)",
            (user["sub"], product_id),
        )
        await db.commit()
    return {"ok": True}


@router.delete("/favorites/{product_id}", responses={401: {"model": ErrorResponse}})
@limiter.limit("60/minute")
async def remove_favorite(product_id: str, request: Request, user: dict = Depends(get_current_user)):
    async with get_db() as db:
        await db.execute(
            "DELETE FROM favorites WHERE user_id = ? AND product_id = ?",
            (user["sub"], product_id),
        )
        await db.commit()
    return {"ok": True}
