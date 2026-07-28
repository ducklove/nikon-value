"""알림 발송 경로(server.notify → server.telegram) 테스트.

httpx를 모킹하므로 텔레그램 API를 실제로 호출하지 않는다.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from server import telegram
from server.notify import send_price_alert

BOT_TOKEN = "123456:test-bot-token-should-never-leak"


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True, "result": {}}

    def json(self):
        return self._payload


class FakeClient:
    """httpx.AsyncClient 대체. calls에 요청을 기록하고 정해진 응답/예외를 돌려준다."""

    calls: list = []
    response: object = FakeResponse()
    error: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, timeout=None):
        FakeClient.calls.append({"url": url, "json": json, "timeout": timeout})
        if FakeClient.error is not None:
            raise FakeClient.error
        return FakeClient.response


@pytest.fixture
def fake_httpx(monkeypatch):
    FakeClient.calls = []
    FakeClient.response = FakeResponse()
    FakeClient.error = None
    monkeypatch.setattr(telegram.httpx, "AsyncClient", FakeClient)
    return FakeClient


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(telegram, "TELEGRAM_BOT_TOKEN", BOT_TOKEN)


async def _linked_user(db, chat_id="90001", provider_id="g-notify"):
    await db.execute(
        "INSERT INTO users (provider, provider_id, name) VALUES (?, ?, ?)",
        ("google", provider_id, "Notify Tester"),
    )
    await db.commit()
    cursor = await db.execute("SELECT id FROM users WHERE provider_id = ?", (provider_id,))
    user_id = (await cursor.fetchone())["id"]
    if chat_id:
        await db.execute(
            "UPDATE users SET telegram_chat_id = ?, telegram_linked_at = datetime('now') WHERE id = ?",
            (chat_id, user_id),
        )
        await db.commit()
    return user_id


@pytest.mark.asyncio
async def test_returns_false_without_bot_token(db, fake_httpx, monkeypatch):
    monkeypatch.setattr(telegram, "TELEGRAM_BOT_TOKEN", "")
    user_id = await _linked_user(db)
    assert await send_price_alert(user_id, "제목", "본문") is False
    assert fake_httpx.calls == []  # 네트워크를 건드리지 않는다


@pytest.mark.asyncio
async def test_returns_false_when_user_has_no_chat_id(db, fake_httpx, configured):
    user_id = await _linked_user(db, chat_id=None, provider_id="g-notify-nolink")
    assert await send_price_alert(user_id, "제목", "본문") is False
    assert fake_httpx.calls == []


@pytest.mark.asyncio
async def test_returns_false_for_unknown_user(db, fake_httpx, configured):
    assert await send_price_alert(9999, "제목", "본문") is False
    assert fake_httpx.calls == []


@pytest.mark.asyncio
async def test_sends_message_to_linked_chat(db, fake_httpx, configured):
    user_id = await _linked_user(db)
    assert await send_price_alert(user_id, "제목", "본문") is True

    assert len(fake_httpx.calls) == 1
    call = fake_httpx.calls[0]
    assert call["url"].endswith("/sendMessage")
    assert call["json"]["chat_id"] == "90001"
    assert "제목" in call["json"]["text"]
    assert "본문" in call["json"]["text"]


@pytest.mark.asyncio
async def test_api_error_returns_false(db, fake_httpx, configured):
    user_id = await _linked_user(db)
    fake_httpx.response = FakeResponse(payload={"ok": False, "error_code": 403, "description": "blocked"})
    assert await send_price_alert(user_id, "제목", "본문") is False


@pytest.mark.asyncio
async def test_http_error_status_returns_false(db, fake_httpx, configured):
    user_id = await _linked_user(db)
    fake_httpx.response = FakeResponse(status_code=502, payload={})
    assert await send_price_alert(user_id, "제목", "본문") is False


@pytest.mark.asyncio
async def test_network_error_returns_false(db, fake_httpx, configured):
    user_id = await _linked_user(db)
    fake_httpx.error = httpx.ConnectError("no route to host")
    assert await send_price_alert(user_id, "제목", "본문") is False


@pytest.mark.asyncio
async def test_malformed_json_returns_false(db, fake_httpx, configured):
    user_id = await _linked_user(db)

    class BadJson(FakeResponse):
        def json(self):
            raise ValueError("not json")

    fake_httpx.response = BadJson()
    assert await send_price_alert(user_id, "제목", "본문") is False


@pytest.mark.asyncio
async def test_bot_token_never_appears_in_logs(db, fake_httpx, configured, caplog):
    """예외 문자열에 요청 URL(= 토큰)이 섞여 들어가지 않는지 확인한다."""
    user_id = await _linked_user(db)
    leaky_url = telegram._api_url("sendMessage")
    request = httpx.Request("POST", leaky_url)
    fake_httpx.error = httpx.HTTPStatusError(
        f"Server error for url {leaky_url}",
        request=request,
        response=httpx.Response(500, request=request),
    )

    with caplog.at_level(logging.DEBUG):
        assert await send_price_alert(user_id, "제목", "본문") is False

    assert caplog.text  # 실패는 로그로 남는다
    assert BOT_TOKEN not in caplog.text
    assert "HTTPStatusError(status=500)" in caplog.text
