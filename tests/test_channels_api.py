from __future__ import annotations

import pytest

from server import notify
from server.auth.jwt import create_token
from server.database import get_db


async def _auth_headers(provider_id="g-ch-100"):
    async with get_db() as db:
        await db.execute(
            "INSERT INTO users (provider, provider_id, email, name) VALUES (?, ?, ?, ?)",
            ("google", provider_id, "test@test.com", "Test"),
        )
        await db.commit()
        cursor = await db.execute("SELECT id FROM users WHERE provider_id = ?", (provider_id,))
        user_id = (await cursor.fetchone())["id"]
    token = create_token(user_id=user_id, provider="google")
    return user_id, {"Authorization": f"Bearer {token}"}


def _fake_telegram(monkeypatch, *, result=True):
    class FakeTelegram:
        def __init__(self, bot_token, chat_id):
            self.chat_id = chat_id

        async def send(self, subject, body):
            return result

    monkeypatch.setattr(notify, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(notify, "TelegramChannel", FakeTelegram)


@pytest.mark.asyncio
async def test_channels_require_auth(client):
    assert (await client.get("/api/channels")).status_code == 401
    assert (await client.put("/api/channels/telegram", json={"chat_id": "1"})).status_code == 401
    assert (await client.delete("/api/channels/telegram")).status_code == 401


@pytest.mark.asyncio
async def test_telegram_channel_roundtrip(client):
    _, headers = await _auth_headers("g-ch-roundtrip")

    resp = await client.get("/api/channels", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"channels": []}

    resp = await client.put("/api/channels/telegram", json={"chat_id": "111222333"}, headers=headers)
    assert resp.status_code == 200

    resp = await client.get("/api/channels", headers=headers)
    assert resp.json() == {
        "channels": [{"channel": "telegram", "config": {"chat_id": "111222333"}}]
    }

    # 같은 채널 재등록은 chat_id를 덮어쓴다
    resp = await client.put("/api/channels/telegram", json={"chat_id": "-444555"}, headers=headers)
    assert resp.status_code == 200
    resp = await client.get("/api/channels", headers=headers)
    assert resp.json()["channels"][0]["config"]["chat_id"] == "-444555"

    resp = await client.delete("/api/channels/telegram", headers=headers)
    assert resp.status_code == 200
    resp = await client.get("/api/channels", headers=headers)
    assert resp.json() == {"channels": []}


@pytest.mark.asyncio
async def test_put_rejects_invalid_chat_id(client):
    _, headers = await _auth_headers("g-ch-invalid")
    for bad in ["abc", "123abc", "", "1" * 21]:
        resp = await client.put("/api/channels/telegram", json={"chat_id": bad}, headers=headers)
        assert resp.status_code == 422, bad


@pytest.mark.asyncio
async def test_test_endpoint_without_channel_returns_404(client):
    _, headers = await _auth_headers("g-ch-test-404")
    resp = await client.post("/api/channels/telegram/test", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_test_endpoint_sends_message(client, monkeypatch):
    _, headers = await _auth_headers("g-ch-test-ok")
    await client.put("/api/channels/telegram", json={"chat_id": "777"}, headers=headers)
    _fake_telegram(monkeypatch, result=True)

    resp = await client.post("/api/channels/telegram/test", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_test_endpoint_reports_send_failure(client, monkeypatch):
    _, headers = await _auth_headers("g-ch-test-fail")
    await client.put("/api/channels/telegram", json={"chat_id": "888"}, headers=headers)
    _fake_telegram(monkeypatch, result=False)

    resp = await client.post("/api/channels/telegram/test", headers=headers)
    assert resp.status_code == 502
    assert resp.json()["error"] == "send_failed"


@pytest.mark.asyncio
async def test_user_delete_cascades_channels(client):
    user_id, headers = await _auth_headers("g-ch-cascade")
    await client.put("/api/channels/telegram", json={"chat_id": "999"}, headers=headers)

    resp = await client.delete("/api/me", headers=headers)
    assert resp.status_code == 200
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT 1 FROM notification_channels WHERE user_id = ?", (user_id,)
        )
        assert await cursor.fetchone() is None
