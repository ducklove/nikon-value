"""기존 운영 DB에 대한 마이그레이션 검증.

SCHEMA는 CREATE TABLE IF NOT EXISTS라 이미 존재하는 users 테이블에는 새 컬럼이
추가되지 않는다. init_db()의 마이그레이션이 기존 DB/신규 DB 모두에서 동작하고,
여러 번 실행해도 안전한지(멱등) 확인한다.
"""

from __future__ import annotations

import sqlite3

import aiosqlite
import pytest

from server.database import close_db, get_db, init_db

# 텔레그램 연동 이전(2026-06)의 스키마.
LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    email       TEXT,
    name        TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    last_login  TEXT DEFAULT (datetime('now')),
    UNIQUE(provider, provider_id)
);

CREATE TABLE IF NOT EXISTS favorites (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id  TEXT NOT NULL,
    added_at    TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, product_id)
);

CREATE TABLE IF NOT EXISTS price_alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id   TEXT NOT NULL,
    target_price REAL NOT NULL,
    triggered    INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, product_id)
);
"""


async def _make_legacy_db(path: str) -> None:
    conn = await aiosqlite.connect(path)
    await conn.executescript(LEGACY_SCHEMA)
    await conn.execute(
        "INSERT INTO users (provider, provider_id, name) VALUES ('google', 'legacy-1', '기존 사용자')"
    )
    await conn.execute(
        "INSERT INTO price_alerts (user_id, product_id, target_price, triggered) VALUES (1, 'nikon-z9', 1000.0, 0)"
    )
    await conn.commit()
    await conn.close()


async def _columns(db, table: str) -> set[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cursor.fetchall()}


async def _tables(db) -> set[str]:
    cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in await cursor.fetchall()}


@pytest.mark.asyncio
async def test_migration_upgrades_existing_db_without_data_loss(tmp_path):
    path = str(tmp_path / "legacy.db")
    await _make_legacy_db(path)

    await init_db(path)
    try:
        async with get_db() as db:
            assert "telegram_chat_id" in await _columns(db, "users")
            assert "telegram_linked_at" in await _columns(db, "users")
            # 새 테이블도 함께 생성된다.
            assert {"telegram_link_codes", "app_state"} <= await _tables(db)
            # 기존 데이터는 그대로 남고, 새 컬럼은 NULL(미연동)이다.
            cursor = await db.execute("SELECT name, telegram_chat_id FROM users WHERE provider_id = 'legacy-1'")
            row = await cursor.fetchone()
            assert row["name"] == "기존 사용자"
            assert row["telegram_chat_id"] is None
            cursor = await db.execute("SELECT COUNT(*) AS cnt FROM price_alerts")
            assert (await cursor.fetchone())["cnt"] == 1
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_migration_is_idempotent_across_restarts(tmp_path):
    path = str(tmp_path / "legacy.db")
    await _make_legacy_db(path)

    # 재기동을 세 번 흉내 낸다 — 두 번째부터는 이미 컬럼이 있으므로 ALTER를 건너뛴다.
    for _ in range(3):
        await init_db(path)
        async with get_db() as db:
            await db.execute(
                "UPDATE users SET telegram_chat_id = '123' WHERE provider_id = 'legacy-1'"
            )
            await db.commit()
        await close_db()

    await init_db(path)
    try:
        async with get_db() as db:
            cursor = await db.execute("SELECT telegram_chat_id FROM users WHERE provider_id = 'legacy-1'")
            assert (await cursor.fetchone())["telegram_chat_id"] == "123"
            cursor = await db.execute("PRAGMA table_info(users)")
            names = [row[1] for row in await cursor.fetchall()]
            # 컬럼이 중복 추가되지 않았다.
            assert names.count("telegram_chat_id") == 1
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_fresh_db_has_telegram_columns(db):
    assert "telegram_chat_id" in await _columns(db, "users")
    assert "telegram_linked_at" in await _columns(db, "users")
    assert {"telegram_link_codes", "app_state"} <= await _tables(db)


@pytest.mark.asyncio
async def test_chat_id_is_unique_but_null_allowed(db):
    for pid in ("u-a", "u-b", "u-c"):
        await db.execute(
            "INSERT INTO users (provider, provider_id) VALUES ('google', ?)", (pid,)
        )
    await db.commit()
    # 미연동 사용자(NULL)는 여러 명이어도 UNIQUE 인덱스에 걸리지 않는다.
    cursor = await db.execute("SELECT COUNT(*) AS cnt FROM users WHERE telegram_chat_id IS NULL")
    assert (await cursor.fetchone())["cnt"] == 3

    await db.execute("UPDATE users SET telegram_chat_id = '999' WHERE provider_id = 'u-a'")
    await db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute("UPDATE users SET telegram_chat_id = '999' WHERE provider_id = 'u-b'")
        await db.commit()
