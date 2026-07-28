"""가격 알림 발송 채널.

이메일(SMTP) 연동은 제거되었고, 현재는 텔레그램으로 발송한다(카카오톡은 미정).
발송이 불가능한 상황 — 봇 토큰 미설정, 사용자가 텔레그램을 연동하지 않음,
네트워크·API 오류 — 에서는 예외를 던지지 않고 False를 반환한다. 체커가
'발송 성공 시에만 triggered 갱신' 규칙을 따르므로, 대기 중이던 알림은
채널이 준비되는 즉시 다음 점검 주기에 자동으로 발송된다.

채널이 늘어나면 send_price_alert가 사용자별 채널 설정에 따라 라우팅하면 된다.
"""

from __future__ import annotations

import logging

from server import telegram

logger = logging.getLogger(__name__)


async def send_price_alert(user_id: int, subject: str, body: str) -> bool:
    """알림을 발송하고 성공 여부를 반환한다. 실패 시 알림은 대기 상태로 남는다."""
    if not telegram.is_configured():
        logger.debug(
            "Telegram bot token not configured; alert for user %d stays pending (%s)",
            user_id,
            subject,
        )
        return False

    chat_id = await telegram.get_chat_id(user_id)
    if not chat_id:
        logger.debug(
            "User %d has no linked Telegram chat; alert stays pending (%s)", user_id, subject
        )
        return False

    return await telegram.send_message(chat_id, f"{subject}\n\n{body}")
