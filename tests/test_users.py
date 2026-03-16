from __future__ import annotations

import pytest

from server.auth.jwt import create_token
from server.database import get_db


async def _create_user(provider="google", provider_id="g-100"):
    async with get_db() as db:
        await db.execute(
            "INSERT INTO users (provider, provider_id, email, name) VALUES (?, ?, ?, ?)",
            (provider, provider_id, "test@test.com", "Test"),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT id FROM users WHERE provider_id = ?", (provider_id,)
        )
        return (await cursor.fetchone())["id"]


@pytest.mark.asyncio
async def test_get_me(client):
    user_id = await _create_user()
    token = create_token(user_id=user_id, provider="google")
    resp = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == user_id
    assert data["provider"] == "google"


@pytest.mark.asyncio
async def test_get_me_unauthorized(client):
    resp = await client.get("/api/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_me(client):
    user_id = await _create_user(provider_id="g-delete")
    token = create_token(user_id=user_id, provider="google")
    resp = await client.delete("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        assert await cursor.fetchone() is None
