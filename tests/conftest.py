from __future__ import annotations

import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("CATALOG_URL", "")  # prevent external HTTP in tests

from server.database import close_db, get_db, init_db  # noqa: E402


@pytest_asyncio.fixture
async def db():
    """Standalone DB fixture for unit tests (no FastAPI app)."""
    await init_db()
    async with get_db() as conn:
        yield conn
    await close_db()


@pytest_asyncio.fixture
async def client():
    """AsyncClient wrapping FastAPI app. lifespan handles DB init/close."""
    from server.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
