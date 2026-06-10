from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest
from notifykit import Channel, KakaoMemoChannel
from notifykit.kakao import _TEXT_LIMIT


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _token() -> str | None:
    return "kakao-access-token"


def _template_from(request_content: bytes) -> dict:
    form = parse_qs(request_content.decode())
    return json.loads(form["template_object"][0])


@pytest.mark.asyncio
async def test_send_success():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["template"] = _template_from(request.content)
        return httpx.Response(200, json={"result_code": 0})

    async with _client(handler) as client:
        channel = KakaoMemoChannel(_token, link_url="https://example.com/p/z9", client=client)
        assert await channel.send("제목", "본문 내용") is True

    assert seen["url"] == "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    assert seen["auth"] == "Bearer kakao-access-token"
    assert seen["template"]["object_type"] == "text"
    assert seen["template"]["text"] == "제목\n\n본문 내용"
    assert seen["template"]["link"]["web_url"] == "https://example.com/p/z9"
    assert seen["template"]["link"]["mobile_web_url"] == "https://example.com/p/z9"


@pytest.mark.asyncio
async def test_text_truncated_to_limit():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["template"] = _template_from(request.content)
        return httpx.Response(200, json={"result_code": 0})

    async with _client(handler) as client:
        channel = KakaoMemoChannel(_token, link_url="https://example.com", client=client)
        assert await channel.send("제목", "가" * 500) is True

    assert len(seen["template"]["text"]) == _TEXT_LIMIT


@pytest.mark.asyncio
async def test_send_without_token_skips_network():
    async def no_token() -> str | None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("토큰이 없으면 네트워크 호출이 없어야 한다")

    async with _client(handler) as client:
        channel = KakaoMemoChannel(no_token, link_url="https://example.com", client=client)
        assert await channel.send("제목", "본문") is False


@pytest.mark.asyncio
async def test_send_api_error_returns_false():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"msg": "this access token does not exist", "code": -401})

    async with _client(handler) as client:
        channel = KakaoMemoChannel(_token, link_url="https://example.com", client=client)
        assert await channel.send("제목", "본문") is False


@pytest.mark.asyncio
async def test_send_network_error_returns_false():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with _client(handler) as client:
        channel = KakaoMemoChannel(_token, link_url="https://example.com", client=client)
        assert await channel.send("제목", "본문") is False


def test_satisfies_channel_protocol():
    assert isinstance(KakaoMemoChannel(_token, link_url="https://example.com"), Channel)
