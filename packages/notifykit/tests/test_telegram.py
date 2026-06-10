from __future__ import annotations

import json

import httpx
import pytest
from notifykit import Channel, TelegramChannel


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_send_success():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as client:
        channel = TelegramChannel("bot-token", "12345", client=client)
        assert await channel.send("제목", "본문 내용") is True

    assert seen["url"] == "https://api.telegram.org/botbot-token/sendMessage"
    assert seen["payload"]["chat_id"] == "12345"
    assert seen["payload"]["text"] == "제목\n\n본문 내용"


@pytest.mark.asyncio
async def test_send_api_error_returns_false():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})

    async with _client(handler) as client:
        channel = TelegramChannel("bot-token", "999", client=client)
        assert await channel.send("제목", "본문") is False


@pytest.mark.asyncio
async def test_send_not_ok_body_returns_false():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False})

    async with _client(handler) as client:
        channel = TelegramChannel("bot-token", "12345", client=client)
        assert await channel.send("제목", "본문") is False


@pytest.mark.asyncio
async def test_send_network_error_returns_false():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with _client(handler) as client:
        channel = TelegramChannel("bot-token", "12345", client=client)
        assert await channel.send("제목", "본문") is False


@pytest.mark.asyncio
async def test_send_without_credentials_skips_network():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("자격 정보가 없으면 네트워크 호출이 없어야 한다")

    async with _client(handler) as client:
        assert await TelegramChannel("", "12345", client=client).send("s", "b") is False
        assert await TelegramChannel("bot-token", "", client=client).send("s", "b") is False


def test_satisfies_channel_protocol():
    assert isinstance(TelegramChannel("t", "c"), Channel)
