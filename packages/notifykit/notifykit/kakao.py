from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_API_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
_TEXT_LIMIT = 200  # 카카오 텍스트 템플릿 제한

# 호출 시점에 유효한(필요하면 갱신된) 액세스 토큰을 돌려준다. 없으면 None.
TokenProvider = Callable[[], Awaitable[str | None]]


class KakaoMemoChannel:
    """카카오톡 '나에게 보내기' API 채널.

    talk_message 동의항목이 포함된 사용자 액세스 토큰이 필요하다.
    토큰 저장·갱신은 앱의 인증 계층 소관이므로, 이 채널은 토큰을 들고 있지 않고
    token_provider 콜백으로 공급받는다.
    """

    def __init__(
        self,
        token_provider: TokenProvider,
        *,
        link_url: str,  # 텍스트 템플릿의 link는 카카오 API 필수 항목
        client: httpx.AsyncClient | None = None,
    ):
        self._token_provider = token_provider
        self._link_url = link_url
        self._client = client  # 주입 시(테스트 등) 수명 관리는 호출자 책임

    async def send(self, subject: str, body: str) -> bool:
        token = await self._token_provider()
        if not token:
            logger.warning("Kakao memo channel has no access token; skipping send")
            return False

        template = {
            "object_type": "text",
            "text": f"{subject}\n\n{body}"[:_TEXT_LIMIT],
            "link": {"web_url": self._link_url, "mobile_web_url": self._link_url},
        }
        headers = {"Authorization": f"Bearer {token}"}
        data = {"template_object": json.dumps(template, ensure_ascii=False)}
        try:
            if self._client is not None:
                resp = await self._client.post(_API_URL, headers=headers, data=data)
            else:
                async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                    resp = await client.post(_API_URL, headers=headers, data=data)
        except httpx.HTTPError as exc:
            logger.warning("Kakao memo send failed: %s", type(exc).__name__)
            return False

        if resp.status_code == 200:
            try:
                if resp.json().get("result_code") == 0:
                    return True
            except ValueError:
                pass
        logger.warning("Kakao memo API error (status=%d)", resp.status_code)
        return False
