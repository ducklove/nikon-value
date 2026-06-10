"""알림 채널 설정 API.

사용자가 가격 알림을 받을 채널을 등록/해제한다. 현재는 텔레그램만 지원하며,
카카오톡(나에게 보내기)은 토큰 영속화가 갖춰지면 별도 엔드포인트로 추가한다.

텔레그램 연결 절차: @BotFather로 만든 봇에게 사용자가 먼저 말을 건 뒤,
자신의 chat_id(@userinfobot 또는 getUpdates로 확인)를 PUT으로 등록하고
POST /api/channels/telegram/test로 수신을 확인한다.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from server.auth.jwt import get_current_user
from server.database import get_db
from server.models import ChannelsResponse, ErrorResponse, TelegramChannelRequest
from server.notify import build_channel
from server.rate_limit import limiter

router = APIRouter(prefix="/api")


@router.get("/channels", response_model=ChannelsResponse, responses={401: {"model": ErrorResponse}})
@limiter.limit("60/minute")
async def get_channels(request: Request, user: dict = Depends(get_current_user)):
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT channel, config FROM notification_channels WHERE user_id = ? ORDER BY channel",
            (user["sub"],),
        )
        rows = await cursor.fetchall()
    return ChannelsResponse(
        channels=[{"channel": r["channel"], "config": json.loads(r["config"])} for r in rows]
    )


@router.put("/channels/telegram", responses={401: {"model": ErrorResponse}})
@limiter.limit("60/minute")
async def upsert_telegram_channel(
    payload: TelegramChannelRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    config = json.dumps({"chat_id": payload.chat_id})
    async with get_db() as db:
        await db.execute(
            """INSERT INTO notification_channels (user_id, channel, config)
               VALUES (?, 'telegram', ?)
               ON CONFLICT(user_id, channel) DO UPDATE SET
                 config = excluded.config,
                 updated_at = datetime('now')""",
            (user["sub"], config),
        )
        await db.commit()
    return {"ok": True}


@router.delete("/channels/telegram", responses={401: {"model": ErrorResponse}})
@limiter.limit("60/minute")
async def remove_telegram_channel(request: Request, user: dict = Depends(get_current_user)):
    async with get_db() as db:
        await db.execute(
            "DELETE FROM notification_channels WHERE user_id = ? AND channel = 'telegram'",
            (user["sub"],),
        )
        await db.commit()
    return {"ok": True}


@router.post(
    "/channels/telegram/test",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
@limiter.limit("5/minute")
async def test_telegram_channel(request: Request, user: dict = Depends(get_current_user)):
    """등록된 텔레그램 채널로 테스트 메시지를 보내 연결을 확인한다."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT config FROM notification_channels WHERE user_id = ? AND channel = 'telegram'",
            (user["sub"],),
        )
        row = await cursor.fetchone()
    if row is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "not_found", "message": "등록된 텔레그램 채널이 없습니다"},
        )

    channel = build_channel("telegram", row["config"])
    sent = channel is not None and await channel.send(
        "[Nikon Value] 알림 채널 연결 확인",
        "이 메시지가 보이면 텔레그램 알림이 정상적으로 연결된 것입니다.",
    )
    if not sent:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": "send_failed", "message": "발송에 실패했습니다 — 봇 토큰과 chat_id를 확인하세요"},
        )
    return {"ok": True}
