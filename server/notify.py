"""가격 알림 발송 라우팅.

채널 구현(텔레그램·카카오톡)은 공통 패키지 notifykit에 있고, 이 모듈은
사용자별 채널 설정(notification_channels 테이블)을 읽어 발송을 위임한다.

채널이 없거나 발송에 실패하면 False를 반환한다 — 체커가 '발송 성공 시에만
triggered 갱신' 규칙을 따르므로, 채널을 연결하면 대기 중이던 알림이 다음
점검 주기에 자동으로 발송된다.

카카오톡(나에게 보내기)은 talk_message 스코프와 사용자 토큰 영속화가
auth 쪽에 갖춰지면 build_channel에 분기를 추가해 연동한다.
"""

from __future__ import annotations

import json
import logging

from notifykit import Channel, TelegramChannel

from server.config import TELEGRAM_BOT_TOKEN
from server.database import get_db

logger = logging.getLogger(__name__)


def build_channel(channel: str, config_json: str) -> Channel | None:
    """notification_channels 행으로부터 notifykit 채널을 만든다. 못 만들면 None."""
    try:
        config = json.loads(config_json)
    except json.JSONDecodeError:
        logger.warning("Invalid channel config JSON (channel=%s)", channel)
        return None

    if channel == "telegram":
        if not TELEGRAM_BOT_TOKEN:
            logger.warning("TELEGRAM_BOT_TOKEN not set; telegram channel disabled")
            return None
        chat_id = str(config.get("chat_id") or "")
        if not chat_id:
            return None
        return TelegramChannel(TELEGRAM_BOT_TOKEN, chat_id)

    logger.warning("Unknown notification channel: %s", channel)
    return None


async def send_price_alert(user_id: int, subject: str, body: str) -> bool:
    """사용자가 등록한 채널로 알림을 발송하고 성공 여부를 반환한다.

    여러 채널이 등록된 경우 첫 성공에서 멈춘다(중복 수신 방지).
    """
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT channel, config FROM notification_channels WHERE user_id = ? ORDER BY channel",
            (user_id,),
        )
        rows = await cursor.fetchall()

    if not rows:
        logger.debug("No notification channel for user %d; alert stays pending (%s)", user_id, subject)
        return False

    for row in rows:
        channel = build_channel(row["channel"], row["config"])
        if channel is not None and await channel.send(subject, body):
            return True
    return False
