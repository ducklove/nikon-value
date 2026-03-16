from __future__ import annotations

import pytest

from server.auth.jwt import create_token
from server.database import get_db


async def _setup_user(pid="g-fav"):
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (provider, provider_id, name) VALUES (?, ?, ?)",
            ("google", pid, "Fav Tester"),
        )
        await db.commit()
        cursor = await db.execute("SELECT id FROM users WHERE provider_id = ?", (pid,))
        return (await cursor.fetchone())["id"]


def _auth(user_id):
    token = create_token(user_id=user_id, provider="google")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_empty_favorites(client):
    uid = await _setup_user("g-empty")
    resp = await client.get("/api/favorites", headers=_auth(uid))
    assert resp.status_code == 200
    assert resp.json()["favorites"] == []


@pytest.mark.asyncio
async def test_add_favorite(client):
    uid = await _setup_user("g-add")
    resp = await client.put("/api/favorites/nikon-z9", headers=_auth(uid))
    assert resp.status_code == 200
    resp = await client.get("/api/favorites", headers=_auth(uid))
    assert "nikon-z9" in resp.json()["favorites"]


@pytest.mark.asyncio
async def test_add_duplicate_is_idempotent(client):
    uid = await _setup_user("g-dup")
    await client.put("/api/favorites/nikon-z9", headers=_auth(uid))
    resp = await client.put("/api/favorites/nikon-z9", headers=_auth(uid))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_remove_favorite(client):
    uid = await _setup_user("g-rm")
    await client.put("/api/favorites/nikon-z9", headers=_auth(uid))
    resp = await client.delete("/api/favorites/nikon-z9", headers=_auth(uid))
    assert resp.status_code == 200
    resp = await client.get("/api/favorites", headers=_auth(uid))
    assert "nikon-z9" not in resp.json()["favorites"]


@pytest.mark.asyncio
async def test_favorites_limit(client):
    uid = await _setup_user("g-limit")
    headers = _auth(uid)
    for i in range(50):
        await client.put(f"/api/favorites/product-{i}", headers=headers)
    resp = await client.put("/api/favorites/product-51", headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"] == "limit_exceeded"


@pytest.mark.asyncio
async def test_unauthorized_favorites(client):
    resp = await client.get("/api/favorites")
    assert resp.status_code == 401
