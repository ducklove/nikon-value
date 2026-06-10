"""가격 알림 발송 채널.

이메일(SMTP) 연동은 제거되었고, 텔레그램·카카오톡 연동이 예정되어 있다.
채널이 구성되기 전까지는 발송하지 않고 False를 반환한다 — 체커가
'발송 성공 시에만 triggered 갱신' 규칙을 따르므로, 채널이 연결되면
대기 중이던 알림이 다음 점검 주기에 자동으로 발송된다.

차후 연동 시 이 모듈에 채널별 구현을 추가하고 send_price_alert가
사용자별 채널 설정에 따라 라우팅하면 된다.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def send_price_alert(user_id: int, subject: str, body: str) -> bool:
    """알림을 발송하고 성공 여부를 반환한다. 현재는 채널 미구성으로 항상 False."""
    logger.debug("No notification channel configured; alert for user %d stays pending (%s)", user_id, subject)
    return False
