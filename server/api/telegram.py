"""텔레그램 연동 API.

CORS 허용 메서드가 GET/PUT/DELETE/OPTIONS라 코드 발급도 PUT을 쓴다. 의미상으로도
'이 사용자의 대기 중 연동 코드를 새로 만든다(기존 코드는 무효화)'는 멱등한 갱신이라
프로젝트의 다른 생성 엔드포인트(관심목록·가격알림의 PUT)와 결이 같다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from server import telegram
from server.auth.jwt import get_current_user
from server.models import ErrorResponse, TelegramLinkCodeResponse, TelegramStatusResponse
from server.rate_limit import limiter

router = APIRouter(prefix="/api/me/telegram")


@router.get("", response_model=TelegramStatusResponse, responses={401: {"model": ErrorResponse}})
@limiter.limit("60/minute")
async def get_telegram_status(request: Request, user: dict = Depends(get_current_user)):
    return TelegramStatusResponse(**await telegram.link_status(user["sub"]))


@router.put(
    "/link-code",
    response_model=TelegramLinkCodeResponse,
    responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
@limiter.limit("10/minute")
async def create_link_code(request: Request, user: dict = Depends(get_current_user)):
    if not telegram.is_configured():
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": "channel_unavailable",
                "message": "텔레그램 봇이 아직 설정되지 않았습니다. 잠시 후 다시 시도해 주세요.",
            },
        )

    code, ttl = await telegram.issue_link_code(user["sub"])
    username = telegram.bot_username()
    return TelegramLinkCodeResponse(
        code=code,
        expires_in=ttl,
        bot_username=username or None,
        deep_link=f"https://t.me/{username}?start={code}" if username else None,
    )


@router.delete("", responses={401: {"model": ErrorResponse}})
@limiter.limit("60/minute")
async def delete_telegram_link(request: Request, user: dict = Depends(get_current_user)):
    unlinked = await telegram.unlink_user(user["sub"])
    return {
        "ok": True,
        "message": "텔레그램 연동을 해제했습니다." if unlinked else "연동된 텔레그램 계정이 없습니다.",
    }
