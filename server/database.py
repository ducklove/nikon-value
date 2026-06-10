from __future__ import annotations

import aiosqlite

from server.config import DB_PATH

_db: aiosqlite.Connection | None = None

SCHEMA = """
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


async def init_db(path: str | None = None) -> None:
    global _db
    db_path = path or DB_PATH
    _db = await aiosqlite.connect(db_path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _db.executescript(SCHEMA)
    await _db.commit()


async def close_db() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None


class get_db:
    """Async context manager that yields the shared DB connection."""

    async def __aenter__(self) -> aiosqlite.Connection:
        if _db is None:
            raise RuntimeError("Database not initialized. Call init_db() first.")
        return _db

    async def __aexit__(self, *exc) -> None:
        pass
