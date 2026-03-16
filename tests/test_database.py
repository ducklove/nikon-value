from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_tables_exist(db):
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in await cursor.fetchall()]
    assert "users" in tables
    assert "favorites" in tables


@pytest.mark.asyncio
async def test_insert_user(db):
    await db.execute(
        "INSERT INTO users (provider, provider_id, email, name) VALUES (?, ?, ?, ?)",
        ("google", "g-123", "test@example.com", "Test User"),
    )
    await db.commit()
    cursor = await db.execute("SELECT * FROM users WHERE provider_id = 'g-123'")
    row = await cursor.fetchone()
    assert row is not None
    assert row["provider"] == "google"
    assert row["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_unique_provider_constraint(db):
    await db.execute(
        "INSERT INTO users (provider, provider_id, email, name) VALUES (?, ?, ?, ?)",
        ("google", "g-unique", "a@example.com", "User A"),
    )
    await db.commit()
    with pytest.raises(Exception):
        await db.execute(
            "INSERT INTO users (provider, provider_id, email, name) VALUES (?, ?, ?, ?)",
            ("google", "g-unique", "b@example.com", "User B"),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_favorites_cascade_delete(db):
    await db.execute(
        "INSERT INTO users (provider, provider_id, name) VALUES (?, ?, ?)",
        ("kakao", "k-del", "Del User"),
    )
    await db.commit()
    cursor = await db.execute("SELECT id FROM users WHERE provider_id = 'k-del'")
    user_id = (await cursor.fetchone())["id"]

    await db.execute(
        "INSERT INTO favorites (user_id, product_id) VALUES (?, ?)",
        (user_id, "nikon-z9"),
    )
    await db.commit()

    await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await db.commit()

    cursor = await db.execute(
        "SELECT * FROM favorites WHERE user_id = ?", (user_id,)
    )
    assert await cursor.fetchone() is None
