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

-- 텔레그램 일회용 연동 코드. 원문 대신 HMAC 해시만 저장하므로 DB가 유출돼도
-- 코드를 역산해 계정을 가로챌 수 없다. 사용 즉시 행을 삭제해 일회성을 보장한다.
CREATE TABLE IF NOT EXISTS telegram_link_codes (
    code_hash  TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);

-- 서버 내부 상태 저장용 key/value (예: 텔레그램 getUpdates offset).
CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# CREATE TABLE IF NOT EXISTS는 이미 존재하는 테이블의 컬럼을 바꾸지 않는다.
# 운영 중인 DB에도 컬럼이 반영되도록 아래 마이그레이션을 매 기동 시 멱등하게 적용한다.
COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("users", "telegram_chat_id", "TEXT"),
    ("users", "telegram_linked_at", "TEXT"),
)

INDEX_MIGRATIONS: tuple[str, ...] = (
    # 텔레그램 계정 하나가 여러 사용자에 연결되지 않도록 한다.
    # SQLite의 UNIQUE 인덱스는 NULL을 중복으로 보지 않으므로 미연동 사용자는 제약을 받지 않는다.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram_chat_id ON users(telegram_chat_id)",
)


async def _migrate(db: aiosqlite.Connection) -> None:
    """기존 운영 DB와 신규 DB 양쪽에서 동작하는 멱등 마이그레이션."""
    for table, column, coltype in COLUMN_MIGRATIONS:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in await cursor.fetchall()}
        if column not in columns:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    for statement in INDEX_MIGRATIONS:
        await db.execute(statement)


async def init_db(path: str | None = None) -> None:
    global _db
    db_path = path or DB_PATH
    _db = await aiosqlite.connect(db_path)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _db.executescript(SCHEMA)
    await _migrate(_db)
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
