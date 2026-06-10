from __future__ import annotations

import json

import pytest

from server import notify
from server.notify import build_channel, send_price_alert


async def _create_user(db, pid="g-notify"):
    await db.execute(
        "INSERT INTO users (provider, provider_id, name) VALUES (?, ?, ?)",
        ("google", pid, "Notify"),
    )
    await db.commit()
    cursor = await db.execute("SELECT id FROM users WHERE provider_id = ?", (pid,))
    return (await cursor.fetchone())["id"]


async def _add_telegram_channel(db, user_id, chat_id="111222333"):
    await db.execute(
        "INSERT INTO notification_channels (user_id, channel, config) VALUES (?, 'telegram', ?)",
        (user_id, json.dumps({"chat_id": chat_id})),
    )
    await db.commit()


def _fake_telegram(monkeypatch, *, result=True):
    """notifykit 호출을 가로채 실제 네트워크 없이 발송을 흉내 낸다."""
    created = []
    sent = []

    class FakeTelegram:
        def __init__(self, bot_token, chat_id):
            created.append({"bot_token": bot_token, "chat_id": chat_id})

        async def send(self, subject, body):
            sent.append({"subject": subject, "body": body})
            return result

    monkeypatch.setattr(notify, "TelegramChannel", FakeTelegram)
    return created, sent


@pytest.mark.asyncio
async def test_no_channel_stays_pending(db):
    user_id = await _create_user(db, pid="g-nochannel")
    assert await send_price_alert(user_id, "제목", "본문") is False


@pytest.mark.asyncio
async def test_telegram_channel_sends(db, monkeypatch):
    user_id = await _create_user(db, pid="g-telegram")
    await _add_telegram_channel(db, user_id, chat_id="111222333")
    monkeypatch.setattr(notify, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    created, sent = _fake_telegram(monkeypatch)

    assert await send_price_alert(user_id, "제목", "본문") is True
    assert created == [{"bot_token": "test-bot-token", "chat_id": "111222333"}]
    assert sent == [{"subject": "제목", "body": "본문"}]


@pytest.mark.asyncio
async def test_send_failure_returns_false(db, monkeypatch):
    user_id = await _create_user(db, pid="g-sendfail")
    await _add_telegram_channel(db, user_id)
    monkeypatch.setattr(notify, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    _fake_telegram(monkeypatch, result=False)

    assert await send_price_alert(user_id, "제목", "본문") is False


@pytest.mark.asyncio
async def test_without_bot_token_stays_pending(db, monkeypatch):
    """봇 토큰 미설정이면 채널이 등록돼 있어도 발송하지 않고 대기 상태를 유지한다."""
    user_id = await _create_user(db, pid="g-notoken")
    await _add_telegram_channel(db, user_id)
    monkeypatch.setattr(notify, "TELEGRAM_BOT_TOKEN", "")

    assert await send_price_alert(user_id, "제목", "본문") is False


def test_build_channel_rejects_bad_rows(monkeypatch):
    monkeypatch.setattr(notify, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    assert build_channel("telegram", "{잘못된 json") is None
    assert build_channel("telegram", "{}") is None  # chat_id 없음
    assert build_channel("pigeon", '{"chat_id": "1"}') is None  # 알 수 없는 채널


def test_build_channel_creates_telegram(monkeypatch):
    monkeypatch.setattr(notify, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    channel = build_channel("telegram", '{"chat_id": "42"}')
    assert channel is not None
