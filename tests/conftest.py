from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# 모노레포 내 공통 패키지를 pip install 없이도 임포트할 수 있게 한다.
_NOTIFYKIT_DIR = str(Path(__file__).resolve().parent.parent / "packages" / "notifykit")
if _NOTIFYKIT_DIR not in sys.path:
    sys.path.insert(0, _NOTIFYKIT_DIR)

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
    from asgi_lifespan import LifespanManager

    from server.main import app

    async with LifespanManager(app) as manager:
        transport = ASGITransport(app=manager.app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
