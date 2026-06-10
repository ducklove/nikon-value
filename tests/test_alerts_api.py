from __future__ import annotations

import pytest

from server.auth.jwt import create_token
from server.config import ALERTS_MAX
from server.database import get_db


async def _setup_user(pid="g-alert"):
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (provider, provider_id, name, email) VALUES (?, ?, ?, ?)",
            ("google", pid, "Alert Tester", "alert@example.com"),
        )
        await db.commit()
        cursor = await db.execute("SELECT id FROM users WHERE provider_id = ?", (pid,))
        return (await cursor.fetchone())["id"]


def _auth(user_id):
    token = create_token(user_id=user_id, provider="google")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_alerts_require_auth(client):
    resp = await client.get("/api/alerts")
    assert resp.status_code == 401
    resp = await client.put("/api/alerts/nikon-z9", json={"target_price": 1000})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_alert_crud_roundtrip(client):
    uid = await _setup_user("g-alert-crud")
    headers = _auth(uid)

    resp = await client.get("/api/alerts", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["alerts"] == []

    resp = await client.put("/api/alerts/nikon-z9", json={"target_price": 2500.5}, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    resp = await client.get("/api/alerts", headers=headers)
    alerts = resp.json()["alerts"]
    assert alerts == [{"product_id": "nikon-z9", "target_price": 2500.5, "triggered": False}]

    # 목표가 변경은 upsert + triggered 재무장
    async with get_db() as db:
        await db.execute("UPDATE price_alerts SET triggered = 1 WHERE user_id = ?", (uid,))
        await db.commit()
    resp = await client.put("/api/alerts/nikon-z9", json={"target_price": 2300}, headers=headers)
    assert resp.status_code == 200
    alerts = (await client.get("/api/alerts", headers=headers)).json()["alerts"]
    assert alerts == [{"product_id": "nikon-z9", "target_price": 2300.0, "triggered": False}]

    resp = await client.delete("/api/alerts/nikon-z9", headers=headers)
    assert resp.status_code == 200
    alerts = (await client.get("/api/alerts", headers=headers)).json()["alerts"]
    assert alerts == []


@pytest.mark.asyncio
async def test_alert_rejects_invalid_target_price(client):
    uid = await _setup_user("g-alert-price")
    headers = _auth(uid)
    resp = await client.put("/api/alerts/nikon-z9", json={"target_price": 0}, headers=headers)
    assert resp.status_code == 422
    resp = await client.put("/api/alerts/nikon-z9", json={"target_price": -10}, headers=headers)
    assert resp.status_code == 422
    resp = await client.put("/api/alerts/nikon-z9", json={"target_price": 2_000_000}, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_alert_rejects_malformed_product_id(client):
    uid = await _setup_user("g-alert-badid")
    headers = _auth(uid)
    resp = await client.put("/api/alerts/NOT%20A%20SLUG", json={"target_price": 100}, headers=headers)
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


@pytest.mark.asyncio
async def test_alert_limit(client):
    uid = await _setup_user("g-alert-limit")
    headers = _auth(uid)
    async with get_db() as db:
        for i in range(ALERTS_MAX):
            await db.execute(
                "INSERT INTO price_alerts (user_id, product_id, target_price) VALUES (?, ?, ?)",
                (uid, f"product-{i}", 100.0),
            )
        await db.commit()

    resp = await client.put("/api/alerts/one-more-product", json={"target_price": 100}, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"] == "limit_exceeded"

    # 기존 알림의 목표가 변경은 한도와 무관하게 가능
    resp = await client.put("/api/alerts/product-0", json={"target_price": 90}, headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_deleting_account_cascades_alerts(client):
    uid = await _setup_user("g-alert-cascade")
    headers = _auth(uid)
    await client.put("/api/alerts/nikon-z9", json={"target_price": 1000}, headers=headers)

    resp = await client.delete("/api/me", headers=headers)
    assert resp.status_code == 200

    async with get_db() as db:
        cursor = await db.execute("SELECT COUNT(*) AS cnt FROM price_alerts WHERE user_id = ?", (uid,))
        assert (await cursor.fetchone())["cnt"] == 0
