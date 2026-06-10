from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


class TelegramChannel:
    """텔레그램 봇 API(sendMessage)로 발송하는 채널.

    받는 사람이 봇에게 먼저 말을 걸어야 봇이 메시지를 보낼 수 있다.
    parse_mode를 쓰지 않으므로 본문을 이스케이프할 필요가 없다.
    """

    def __init__(self, bot_token: str, chat_id: str, *, client: httpx.AsyncClient | None = None):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._client = client  # 주입 시(테스트 등) 수명 관리는 호출자 책임

    async def send(self, subject: str, body: str) -> bool:
        if not self._bot_token or not self._chat_id:
            logger.warning("Telegram channel missing bot_token or chat_id; skipping send")
            return False

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": f"{subject}\n\n{body}",
            "disable_web_page_preview": True,
        }
        try:
            if self._client is not None:
                resp = await self._client.post(url, json=payload)
            else:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    resp = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            # 예외 메시지에 URL(봇 토큰 포함)이 들어갈 수 있어 타입만 남긴다.
            logger.warning("Telegram send failed (chat_id=%s): %s", self._chat_id, type(exc).__name__)
            return False

        if resp.status_code == 200:
            try:
                if resp.json().get("ok") is True:
                    return True
            except ValueError:
                pass
        logger.warning(
            "Telegram API error (chat_id=%s, status=%d): %s",
            self._chat_id,
            resp.status_code,
            _description(resp),
        )
        return False


def _description(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("description", ""))
    except ValueError:
        return ""
