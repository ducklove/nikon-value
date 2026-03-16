from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from server.auth.jwt import get_current_user
from server.database import get_db
from server.models import ErrorResponse, UserResponse

router = APIRouter(prefix="/api")


@router.get("/me", response_model=UserResponse, responses={401: {"model": ErrorResponse}})
async def get_me(user: dict = Depends(get_current_user)):
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, provider, name, email FROM users WHERE id = ?",
            (user["sub"],),
        )
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "사용자를 찾을 수 없습니다"},
        )
    return UserResponse(id=row["id"], provider=row["provider"], name=row["name"], email=row["email"])


@router.delete("/me", responses={401: {"model": ErrorResponse}})
async def delete_me(user: dict = Depends(get_current_user)):
    async with get_db() as db:
        result = await db.execute("DELETE FROM users WHERE id = ?", (user["sub"],))
        await db.commit()
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "사용자를 찾을 수 없습니다"},
        )
    return {"ok": True, "message": "계정이 삭제되었습니다"}
