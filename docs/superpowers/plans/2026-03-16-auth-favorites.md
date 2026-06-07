# OAuth 인증 + 관심 목록 구현 계획

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Google/Naver/Kakao OAuth 인증 + 사용자별 관심 목록 관리 API 서버를 구축하고 기존 정적 사이트와 연동한다.

**Architecture:** 라즈베리파이에서 FastAPI 서버를 운영하고 (cantabile.tplinkdns.com, HTTPS), GitHub Pages 정적 사이트에서 CORS로 호출한다. SQLite에 사용자/관심 목록을 저장하고, catalog.json을 GitHub Pages에서 캐싱한다.

**Tech Stack:** Python 3.13, FastAPI, uvicorn, Authlib, PyJWT, aiosqlite, slowapi, python-dotenv

**Spec:** `docs/superpowers/specs/2026-03-16-auth-favorites-design.md`

---

## File Structure

### 신규 생성 파일

```
server/                          # API 서버 루트
├── __init__.py
├── main.py                      # FastAPI 앱 엔트리포인트, CORS, lifespan
├── config.py                    # 환경 변수 로드 (.env), 설정 상수
├── database.py                  # SQLite 초기화, 커넥션 관리
├── models.py                    # Pydantic 응답 모델
├── rate_limit.py                # slowapi Limiter 인스턴스 (순환 임포트 방지)
├── auth/
│   ├── __init__.py
│   ├── jwt.py                   # JWT 생성/검증, 의존성 (get_current_user)
│   ├── oauth.py                 # OAuth state 생성/검증 (HMAC)
│   ├── routes.py                # /auth/{provider}, /auth/{provider}/callback
│   └── providers.py             # Google/Naver/Kakao OAuth 설정
├── api/
│   ├── __init__.py
│   ├── health.py                # GET /health
│   ├── users.py                 # GET/DELETE /api/me
│   └── favorites.py             # GET/PUT/DELETE /api/favorites
├── catalog.py                   # catalog.json 캐싱 + 주기적 갱신
├── .env.example                 # 환경 변수 템플릿
└── requirements.txt             # API 서버 의존성

tests/
├── conftest.py                  # 테스트 fixtures (TestClient, 테스트 DB)
├── test_database.py             # DB 초기화, 스키마 테스트
├── test_jwt.py                  # JWT 생성/검증 테스트
├── test_oauth_state.py          # OAuth state HMAC 테스트
├── test_health.py               # /health 엔드포인트 테스트
├── test_users.py                # /api/me 테스트
└── test_favorites.py            # 관심 목록 CRUD 테스트

```

### 수정 파일

```
js/auth.js                       # 신규: 프론트엔드 인증 + 관심 목록 연동
scripts/build_static_site.py     # 수정: auth.js <script> 태그 주입
```

---

## Chunk 1: 프로젝트 기반 + 데이터베이스

### Task 1: 프로젝트 구조 및 의존성 설정

**Files:**
- Create: `server/__init__.py` (빈 파일)
- Create: `server/auth/__init__.py` (빈 파일)
- Create: `server/api/__init__.py` (빈 파일)
- Create: `server/requirements.txt`
- Create: `server/.env.example`
- Create: `server/config.py`
- Create: `pyproject.toml` (pytest 설정)

- [ ] **Step 0: 패키지 __init__.py 및 pytest 설정 생성**

```bash
mkdir -p server/auth server/api tests
touch server/__init__.py server/auth/__init__.py server/api/__init__.py tests/__init__.py
```

`pyproject.toml` (프로젝트 루트에 생성):

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 1: server/requirements.txt 작성**

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
authlib>=1.3.0
httpx>=0.27.0
PyJWT>=2.9.0
aiosqlite>=0.20.0
slowapi>=0.1.9
python-dotenv>=1.0.0
pytest>=8.0.0
pytest-asyncio>=0.24.0
```

- [ ] **Step 2: server/.env.example 작성**

```bash
# JWT
JWT_SECRET_KEY=change-me-to-random-string

# Google OAuth
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# Naver OAuth
NAVER_CLIENT_ID=
NAVER_CLIENT_SECRET=

# Kakao OAuth
KAKAO_CLIENT_ID=
KAKAO_CLIENT_SECRET=

# Server
API_BASE_URL=https://cantabile.tplinkdns.com
FRONTEND_URL=https://ducklove.github.io/nikon-value
CATALOG_URL=https://ducklove.github.io/nikon-value/data/catalog.json

# Database
DB_PATH=./data/nikon_api.db
```

- [ ] **Step 3: server/config.py 작성**

```python
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


JWT_SECRET_KEY = _env("JWT_SECRET_KEY")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 7

GOOGLE_CLIENT_ID = _env("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = _env("GOOGLE_CLIENT_SECRET")
NAVER_CLIENT_ID = _env("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = _env("NAVER_CLIENT_SECRET")
KAKAO_CLIENT_ID = _env("KAKAO_CLIENT_ID")
KAKAO_CLIENT_SECRET = _env("KAKAO_CLIENT_SECRET")

API_BASE_URL = _env("API_BASE_URL", "https://cantabile.tplinkdns.com")
FRONTEND_URL = _env("FRONTEND_URL", "https://ducklove.github.io/nikon-value")
CATALOG_URL = _env(
    "CATALOG_URL",
    "https://ducklove.github.io/nikon-value/data/catalog.json",
)

DB_PATH = _env("DB_PATH", str(Path(__file__).parent / "data" / "nikon_api.db"))

FAVORITES_MAX = 50
CATALOG_REFRESH_SECONDS = 3600

OAUTH_STATE_MAX_AGE = 300  # 5 minutes
```

- [ ] **Step 4: 커밋**

```bash
git add server/__init__.py server/auth/__init__.py server/api/__init__.py tests/__init__.py
git add server/requirements.txt server/.env.example server/config.py pyproject.toml
git commit -m "feat: add API server project skeleton and config"
```

---

### Task 2: 데이터베이스 레이어

**Files:**
- Create: `server/database.py`
- Create: `tests/test_database.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: tests/conftest.py 작성**

```python
from __future__ import annotations

import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("CATALOG_URL", "")  # 테스트에서 외부 HTTP 호출 방지

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
    # lifespan handles cleanup
```

**Note:** `client` fixture는 앱의 lifespan이 DB를 초기화하므로 별도 `db` fixture와 함께 쓰지 않는다. API 테스트에서 DB 접근이 필요하면 `get_db()`를 직접 사용한다.

- [ ] **Step 2: tests/test_database.py에 실패하는 테스트 작성**

```python
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
```

- [ ] **Step 3: 테스트 실행 → 실패 확인**

Run: `python -m pytest tests/test_database.py -v`
Expected: FAIL (ModuleNotFoundError: server.database)

- [ ] **Step 4: server/database.py 구현**

```python
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
```

- [ ] **Step 5: 테스트 실행 → 통과 확인**

Note: `__init__.py` 파일들은 Task 1에서 이미 생성됨.

Run: `cd /home/cantabile/Works/nikon_value && python -m pytest tests/test_database.py -v`
Expected: 4 passed

- [ ] **Step 6: 커밋**

```bash
git add server/ tests/
git commit -m "feat: add SQLite database layer with schema and tests"
```

---

## Chunk 2: JWT + OAuth State

### Task 3: JWT 생성/검증

**Files:**
- Create: `server/auth/jwt.py`
- Create: `server/models.py`
- Create: `tests/test_jwt.py`

- [ ] **Step 1: tests/test_jwt.py에 실패하는 테스트 작성**

```python
from __future__ import annotations

import pytest

from server.auth.jwt import create_token, decode_token


def test_create_and_decode_token():
    token = create_token(user_id=42, provider="google")
    payload = decode_token(token)
    assert payload["sub"] == 42
    assert payload["provider"] == "google"


def test_expired_token():
    token = create_token(user_id=1, provider="naver", expire_days=-1)
    with pytest.raises(Exception):
        decode_token(token)


def test_invalid_token():
    with pytest.raises(Exception):
        decode_token("not.a.valid.token")
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python -m pytest tests/test_jwt.py -v`
Expected: FAIL

- [ ] **Step 3: server/models.py 구현**

```python
from __future__ import annotations

from pydantic import BaseModel


class UserResponse(BaseModel):
    id: int
    provider: str
    name: str | None
    email: str | None


class FavoritesResponse(BaseModel):
    favorites: list[str]


class HealthResponse(BaseModel):
    status: str
    db: str
    catalog_loaded: bool
    catalog_products: int
    uptime_seconds: int


class ErrorResponse(BaseModel):
    error: str
    message: str
```

- [ ] **Step 4: server/auth/jwt.py 구현**

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server.config import JWT_ALGORITHM, JWT_EXPIRE_DAYS, JWT_SECRET_KEY

_bearer = HTTPBearer(auto_error=False)


def create_token(
    user_id: int, provider: str, expire_days: int | None = None
) -> str:
    days = expire_days if expire_days is not None else JWT_EXPIRE_DAYS
    exp = datetime.now(timezone.utc) + timedelta(days=days)
    return jwt.encode(
        {"sub": user_id, "provider": provider, "exp": exp},
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def decode_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "인증이 필요합니다"},
        )
    try:
        return decode_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "토큰이 만료되었습니다"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "유효하지 않은 토큰입니다"},
        )
```

- [ ] **Step 5: 테스트 실행 → 통과 확인**

Run: `python -m pytest tests/test_jwt.py -v`
Expected: 3 passed

- [ ] **Step 6: 커밋**

```bash
git add server/models.py server/auth/jwt.py tests/test_jwt.py
git commit -m "feat: add JWT creation/verification with tests"
```

---

### Task 4: OAuth State (CSRF 방지)

**Files:**
- Create: `server/auth/oauth.py`
- Create: `tests/test_oauth_state.py`

- [ ] **Step 1: tests/test_oauth_state.py에 실패하는 테스트 작성**

```python
from __future__ import annotations

import time

import pytest

from server.auth.oauth import create_state, verify_state


def test_create_and_verify_state():
    state = create_state(return_to="/products/nikon-z9.html")
    data = verify_state(state)
    assert data["return_to"] == "/products/nikon-z9.html"


def test_verify_tampered_state():
    state = create_state(return_to="/")
    with pytest.raises(ValueError, match="Invalid"):
        verify_state(state + "tampered")


def test_verify_expired_state(monkeypatch):
    state = create_state(return_to="/")
    # Simulate 6 minutes later (beyond 5 min max age)
    monkeypatch.setattr("server.auth.oauth.time.time", lambda: time.time() + 360)
    with pytest.raises(ValueError, match="expired"):
        verify_state(state)


def test_state_without_return_to():
    state = create_state()
    data = verify_state(state)
    assert data["return_to"] == "/"
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python -m pytest tests/test_oauth_state.py -v`
Expected: FAIL

- [ ] **Step 3: server/auth/oauth.py 구현**

```python
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from server.config import JWT_SECRET_KEY, OAUTH_STATE_MAX_AGE


def create_state(return_to: str = "/") -> str:
    payload = {
        "ts": int(time.time()),
        "nonce": secrets.token_urlsafe(16),
        "return_to": return_to or "/",
    }
    data = json.dumps(payload, separators=(",", ":"))
    sig = _sign(data)
    raw = base64.urlsafe_b64encode(f"{data}|{sig}".encode()).decode()
    return raw


def verify_state(state: str) -> dict:
    try:
        decoded = base64.urlsafe_b64decode(state).decode()
        data, sig = decoded.rsplit("|", 1)
    except Exception:
        raise ValueError("Invalid state format")

    if not hmac.compare_digest(sig, _sign(data)):
        raise ValueError("Invalid state signature")

    payload = json.loads(data)
    age = time.time() - payload["ts"]
    if age > OAUTH_STATE_MAX_AGE:
        raise ValueError("State expired")

    return payload


def _sign(data: str) -> str:
    return hmac.new(
        JWT_SECRET_KEY.encode(), data.encode(), hashlib.sha256
    ).hexdigest()
```

- [ ] **Step 4: 테스트 실행 → 통과 확인**

Run: `python -m pytest tests/test_oauth_state.py -v`
Expected: 4 passed

- [ ] **Step 5: 커밋**

```bash
git add server/auth/oauth.py tests/test_oauth_state.py
git commit -m "feat: add HMAC-based OAuth state for CSRF prevention"
```

---

## Chunk 3: Catalog 캐싱 + Health 엔드포인트 + FastAPI 앱

### Task 5: Catalog 캐싱

**Files:**
- Create: `server/catalog.py`

- [ ] **Step 1: server/catalog.py 구현**

```python
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from server.config import CATALOG_REFRESH_SECONDS, CATALOG_URL

logger = logging.getLogger(__name__)

_product_ids: set[str] = set()
_loaded: bool = False
_refresh_task: asyncio.Task | None = None


async def load_catalog() -> None:
    global _product_ids, _loaded
    if not CATALOG_URL:
        logger.info("CATALOG_URL not set, skipping catalog load")
        _loaded = True  # 테스트 환경에서는 검증 건너뛰기
        return
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(CATALOG_URL, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        ids: set[str] = set()
        for cat in data.get("categories", []):
            for prod in cat.get("products", []):
                pid = prod.get("id")
                if pid:
                    ids.add(pid)
        _product_ids = ids
        _loaded = True
        logger.info("Catalog loaded: %d products", len(ids))
    except Exception:
        logger.exception("Failed to load catalog")
        if not _loaded:
            logger.warning("No cached catalog available")


async def _refresh_loop() -> None:
    while True:
        await asyncio.sleep(CATALOG_REFRESH_SECONDS)
        await load_catalog()


def start_refresh() -> None:
    global _refresh_task
    _refresh_task = asyncio.create_task(_refresh_loop())


def stop_refresh() -> None:
    global _refresh_task
    if _refresh_task:
        _refresh_task.cancel()
        _refresh_task = None


def is_loaded() -> bool:
    return _loaded


def is_valid_product(product_id: str) -> bool:
    return product_id in _product_ids


def product_count() -> int:
    return len(_product_ids)
```

- [ ] **Step 2: 커밋**

```bash
git add server/catalog.py
git commit -m "feat: add catalog.json caching with periodic refresh"
```

---

### Task 6: FastAPI 앱 + Health 엔드포인트

**Files:**
- Create: `server/main.py`
- Create: `server/api/health.py`
- Create: `tests/test_health.py`

- [ ] **Step 1: tests/test_health.py에 실패하는 테스트 작성**

```python
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_endpoint(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "db" in data
    assert "catalog_loaded" in data
    assert "uptime_seconds" in data
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python -m pytest tests/test_health.py -v`
Expected: FAIL

- [ ] **Step 3: server/api/health.py 구현**

```python
from __future__ import annotations

import time

from fastapi import APIRouter

from server import catalog
from server.database import get_db
from server.models import HealthResponse

router = APIRouter()
_start_time: float = 0.0


def set_start_time() -> None:
    global _start_time
    _start_time = time.time()


@router.get("/health", response_model=HealthResponse)
async def health():
    db_status = "ok"
    try:
        async with get_db() as conn:
            await conn.execute("SELECT 1")
    except Exception:
        db_status = "error"

    return HealthResponse(
        status="healthy" if db_status == "ok" else "degraded",
        db=db_status,
        catalog_loaded=catalog.is_loaded(),
        catalog_products=catalog.product_count(),
        uptime_seconds=int(time.time() - _start_time),
    )
```

- [ ] **Step 4: server/main.py 구현**

```python
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from server import catalog
from server.api import health
from server.config import DB_PATH, FRONTEND_URL
from server.database import close_db, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    await init_db()
    await catalog.load_catalog()
    catalog.start_refresh()
    health.set_start_time()
    yield
    # Shutdown
    catalog.stop_refresh()
    await close_db()


app = FastAPI(title="Nikon Value API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_credentials=False,
    allow_methods=["GET", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization"],
)

app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(health.router)
```

- [ ] **Step 5: 테스트 실행 → 통과 확인**

Note: `client` fixture는 이미 conftest.py에 정의되어 있음.

Run: `python -m pytest tests/test_health.py -v`
Expected: 1 passed

- [ ] **Step 6: 커밋**

```bash
git add server/main.py server/api/health.py tests/test_health.py
git commit -m "feat: add FastAPI app with CORS, lifespan, and health endpoint"
```

---

## Chunk 4: 사용자 API + 관심 목록 API

### Task 7: 사용자 API (GET/DELETE /api/me)

**Files:**
- Create: `server/api/users.py`
- Create: `tests/test_users.py`

- [ ] **Step 1: tests/test_users.py에 실패하는 테스트 작성**

```python
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
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python -m pytest tests/test_users.py -v`
Expected: FAIL

- [ ] **Step 3: server/api/users.py 구현**

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from server.auth.jwt import get_current_user
from server.database import get_db
from server.models import ErrorResponse, UserResponse

router = APIRouter(prefix="/api")


@router.get("/me", response_model=UserResponse, responses={401: {"model": ErrorResponse}})
async def get_me(user: dict = Depends(get_current_user)):
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, provider, name, email FROM users WHERE id = ?",
            (user["sub"],),
        )
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "사용자를 찾을 수 없습니다"},
        )
    return UserResponse(id=row["id"], provider=row["provider"], name=row["name"], email=row["email"])


@router.delete("/me", responses={401: {"model": ErrorResponse}})
async def delete_me(user: dict = Depends(get_current_user)):
    async with get_db() as db:
        result = await db.execute("DELETE FROM users WHERE id = ?", (user["sub"],))
        await db.commit()
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "사용자를 찾을 수 없습니다"},
        )
    return {"ok": True, "message": "계정이 삭제되었습니다"}
```

- [ ] **Step 4: server/main.py에 라우터 등록**

`server/main.py`에 추가:

```python
from server.api import users
app.include_router(users.router)
```

- [ ] **Step 5: 테스트 실행 → 통과 확인**

Run: `python -m pytest tests/test_users.py -v`
Expected: 3 passed

- [ ] **Step 6: 커밋**

```bash
git add server/api/users.py tests/test_users.py server/main.py
git commit -m "feat: add GET/DELETE /api/me user endpoints"
```

---

### Task 8: 관심 목록 API

**Files:**
- Create: `server/api/favorites.py`
- Create: `tests/test_favorites.py`

- [ ] **Step 1: tests/test_favorites.py에 실패하는 테스트 작성**

```python
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
```

Note: catalog 캐싱이 비활성(CATALOG_URL="")이므로 `product_id` 검증은 건너뜀. 프로덕션에서는 catalog이 로드된 후 검증이 활성화됨.

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python -m pytest tests/test_favorites.py -v`
Expected: FAIL

- [ ] **Step 3: server/api/favorites.py 구현**

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from server import catalog
from server.auth.jwt import get_current_user
from server.config import FAVORITES_MAX
from server.database import get_db
from server.models import ErrorResponse, FavoritesResponse

router = APIRouter(prefix="/api")


def _check_catalog_ready():
    """catalog 미로드 시 502 반환."""
    if not catalog.is_loaded():
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "service_unavailable", "message": "서버 준비 중입니다"},
        )


@router.get("/favorites", response_model=FavoritesResponse, responses={401: {"model": ErrorResponse}})
async def get_favorites(user: dict = Depends(get_current_user)):
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT product_id FROM favorites WHERE user_id = ? ORDER BY added_at",
            (user["sub"],),
        )
        rows = await cursor.fetchall()
    return FavoritesResponse(favorites=[r["product_id"] for r in rows])


@router.put("/favorites/{product_id}", responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
async def add_favorite(product_id: str, user: dict = Depends(get_current_user)):
    # catalog이 로드된 경우에만 product_id 검증
    if catalog.is_loaded() and not catalog.is_valid_product(product_id):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "not_found", "message": f"존재하지 않는 제품입니다: {product_id}"},
        )

    async with get_db() as db:
        # Check if already exists (idempotent)
        cursor = await db.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND product_id = ?",
            (user["sub"], product_id),
        )
        if await cursor.fetchone():
            return {"ok": True}

        # Check limit
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM favorites WHERE user_id = ?",
            (user["sub"],),
        )
        count = (await cursor.fetchone())["cnt"]
        if count >= FAVORITES_MAX:
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content={"error": "limit_exceeded", "message": f"관심 목록은 최대 {FAVORITES_MAX}개까지 가능합니다"},
            )

        await db.execute(
            "INSERT INTO favorites (user_id, product_id) VALUES (?, ?)",
            (user["sub"], product_id),
        )
        await db.commit()
    return {"ok": True}


@router.delete("/favorites/{product_id}", responses={401: {"model": ErrorResponse}})
async def remove_favorite(product_id: str, user: dict = Depends(get_current_user)):
    async with get_db() as db:
        await db.execute(
            "DELETE FROM favorites WHERE user_id = ? AND product_id = ?",
            (user["sub"], product_id),
        )
        await db.commit()
    return {"ok": True}
```

- [ ] **Step 4: server/main.py에 라우터 등록**

`server/main.py`에 추가:

```python
from server.api import favorites
app.include_router(favorites.router)
```

- [ ] **Step 5: 테스트 실행 → 통과 확인**

Run: `python -m pytest tests/test_favorites.py -v`
Expected: 6 passed

- [ ] **Step 6: 커밋**

```bash
git add server/api/favorites.py tests/test_favorites.py server/main.py
git commit -m "feat: add favorites CRUD API with limit enforcement"
```

---

## Chunk 5: OAuth 인증 라우트

### Task 9: OAuth 제공자 설정

**Files:**
- Create: `server/auth/providers.py`

- [ ] **Step 1: server/auth/providers.py 구현**

```python
from __future__ import annotations

from authlib.integrations.httpx_client import AsyncOAuth2Client

from server.config import (
    API_BASE_URL,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    KAKAO_CLIENT_ID,
    KAKAO_CLIENT_SECRET,
    NAVER_CLIENT_ID,
    NAVER_CLIENT_SECRET,
)

PROVIDERS: dict[str, dict] = {
    "google": {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://www.googleapis.com/oauth2/v3/userinfo",
        "scope": "openid email profile",
        "id_field": "sub",
        "email_field": "email",
        "name_field": "name",
    },
    "naver": {
        "client_id": NAVER_CLIENT_ID,
        "client_secret": NAVER_CLIENT_SECRET,
        "authorize_url": "https://nid.naver.com/oauth2.0/authorize",
        "token_url": "https://nid.naver.com/oauth2.0/token",
        "userinfo_url": "https://openapi.naver.com/v1/nid/me",
        "scope": "",
        "id_field": "response.id",
        "email_field": "response.email",
        "name_field": "response.name",
    },
    "kakao": {
        "client_id": KAKAO_CLIENT_ID,
        "client_secret": KAKAO_CLIENT_SECRET,
        "authorize_url": "https://kauth.kakao.com/oauth/authorize",
        "token_url": "https://kauth.kakao.com/oauth/token",
        "userinfo_url": "https://kapi.kakao.com/v2/user/me",
        "scope": "profile_nickname account_email",
        "id_field": "id",
        "email_field": "kakao_account.email",
        "name_field": "kakao_account.profile.nickname",
    },
}


def get_oauth_client(provider: str) -> AsyncOAuth2Client:
    cfg = PROVIDERS[provider]
    return AsyncOAuth2Client(
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        redirect_uri=f"{API_BASE_URL}/auth/{provider}/callback",
    )


def get_nested(data: dict, dotted_key: str):
    """Retrieve a value from nested dict using dot notation."""
    keys = dotted_key.split(".")
    val = data
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return None
    return val
```

- [ ] **Step 2: 커밋**

```bash
git add server/auth/providers.py
git commit -m "feat: add OAuth provider configs for Google/Naver/Kakao"
```

---

### Task 10: OAuth 인증 라우트

**Files:**
- Create: `server/auth/routes.py`

- [ ] **Step 1: server/auth/routes.py 구현**

```python
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from server.auth.jwt import create_token
from server.auth.oauth import create_state, verify_state
from server.auth.providers import PROVIDERS, get_nested, get_oauth_client
from server.config import FRONTEND_URL
from server.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth")


@router.get("/{provider}")
async def oauth_start(provider: str, request: Request):
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")

    return_to = request.query_params.get("return_to", "/")
    state = create_state(return_to=return_to)

    cfg = PROVIDERS[provider]
    client = get_oauth_client(provider)
    url = client.create_authorization_url(cfg["authorize_url"], state=state, scope=cfg["scope"])
    return RedirectResponse(url[0])


@router.get("/{provider}/callback")
async def oauth_callback(provider: str, code: str = "", state: str = ""):
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")

    # Verify state
    try:
        state_data = verify_state(state)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid state: {e}")

    cfg = PROVIDERS[provider]
    client = get_oauth_client(provider)

    # Exchange code for token
    try:
        token = await client.fetch_token(cfg["token_url"], code=code)
    except Exception:
        logger.exception("Token exchange failed for %s", provider)
        raise HTTPException(status_code=502, detail="OAuth token exchange failed")

    # Fetch user info
    try:
        resp = await client.get(cfg["userinfo_url"])
        resp.raise_for_status()
        userinfo = resp.json()
    except Exception:
        logger.exception("Userinfo fetch failed for %s", provider)
        raise HTTPException(status_code=502, detail="Failed to fetch user info")
    finally:
        await client.aclose()

    provider_id = str(get_nested(userinfo, cfg["id_field"]) or "")
    email = get_nested(userinfo, cfg["email_field"])
    name = get_nested(userinfo, cfg["name_field"])

    if not provider_id:
        raise HTTPException(status_code=502, detail="Could not get user ID from provider")

    # Upsert user
    async with get_db() as db:
        await db.execute(
            """INSERT INTO users (provider, provider_id, email, name)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(provider, provider_id) DO UPDATE SET
                 email = excluded.email,
                 name = excluded.name,
                 last_login = datetime('now')""",
            (provider, provider_id, email, name),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT id FROM users WHERE provider = ? AND provider_id = ?",
            (provider, provider_id),
        )
        user_id = (await cursor.fetchone())["id"]

    jwt_token = create_token(user_id=user_id, provider=provider)
    return_to = state_data.get("return_to", "/")
    redirect_url = f"{FRONTEND_URL}{return_to}#token={jwt_token}"
    return RedirectResponse(redirect_url)
```

- [ ] **Step 2: server/main.py에 라우터 등록**

`server/main.py`에 추가:

```python
from server.auth import routes as auth_routes
app.include_router(auth_routes.router)
```

- [ ] **Step 3: 커밋**

```bash
git add server/auth/routes.py server/main.py
git commit -m "feat: add OAuth login/callback routes for all providers"
```

---

### Task 11: Rate Limiting 적용

**Files:**
- Create: `server/rate_limit.py`
- Modify: `server/main.py`
- Modify: `server/auth/routes.py`
- Modify: `server/api/favorites.py`
- Modify: `server/api/users.py`

- [ ] **Step 1: server/rate_limit.py 생성 (순환 임포트 방지)**

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
```

- [ ] **Step 2: server/main.py에 Limiter 연결**

```python
from server.rate_limit import limiter

app.state.limiter = limiter
```

- [ ] **Step 3: 인증 라우트에 rate limit 적용 (5/min)**

`server/auth/routes.py`의 `oauth_start`, `oauth_callback`에:

```python
from server.rate_limit import limiter

@router.get("/{provider}")
@limiter.limit("5/minute")
async def oauth_start(provider: str, request: Request):
    ...
```

- [ ] **Step 4: API 라우트에 rate limit 적용 (60/min)**

`server/api/users.py`, `server/api/favorites.py`의 각 엔드포인트에:

```python
from server.rate_limit import limiter

@router.get("/me")
@limiter.limit("60/minute")
async def get_me(request: Request, ...):
    ...
```

주의: slowapi는 `request: Request` 파라미터가 필요하므로 각 핸들러에 추가.

- [ ] **Step 4: 전체 테스트 실행**

Run: `python -m pytest tests/ -v`
Expected: All passed

- [ ] **Step 5: 커밋**

```bash
git add server/
git commit -m "feat: add rate limiting to auth (5/min) and API (60/min) endpoints"
```

---

## Chunk 6: 배포 설정

### Task 12: 운영 환경 배포 설정

배포 서비스와 인증서 갱신 훅은 저장소에 고정하지 않고 운영 환경에서 별도로 관리한다.

---

## Chunk 7: 프론트엔드 연동

### Task 13: auth.js 작성

**Files:**
- Create: `js/auth.js`

- [ ] **Step 1: js/auth.js 구현**

```javascript
(function () {
  'use strict';

  const API_BASE = 'https://cantabile.tplinkdns.com';
  const TOKEN_KEY = 'nikon-value-token';

  // --- Token management ---

  function getToken() {
    return localStorage.getItem(TOKEN_KEY);
  }

  function setToken(token) {
    localStorage.setItem(TOKEN_KEY, token);
  }

  function clearToken() {
    localStorage.removeItem(TOKEN_KEY);
  }

  function checkHashToken() {
    const hash = window.location.hash;
    const match = hash.match(/^#token=(.+)$/);
    if (match) {
      setToken(match[1]);
      history.replaceState(null, '', window.location.pathname + window.location.search);
    }
  }

  // --- API calls ---

  async function apiFetch(path, options) {
    const token = getToken();
    const headers = Object.assign({}, options?.headers || {});
    if (token) headers['Authorization'] = 'Bearer ' + token;
    try {
      const resp = await fetch(API_BASE + path, Object.assign({ headers }, options || {}));
      if (resp.status === 401) {
        clearToken();
        renderLoggedOut();
        return null;
      }
      return resp;
    } catch (e) {
      console.warn('API server unreachable:', e.message);
      return null;
    }
  }

  async function fetchMe() {
    const resp = await apiFetch('/api/me');
    if (!resp || !resp.ok) return null;
    return resp.json();
  }

  async function fetchFavorites() {
    const resp = await apiFetch('/api/favorites');
    if (!resp || !resp.ok) return [];
    const data = await resp.json();
    return data.favorites || [];
  }

  async function addFavorite(productId) {
    return apiFetch('/api/favorites/' + encodeURIComponent(productId), { method: 'PUT' });
  }

  async function removeFavorite(productId) {
    return apiFetch('/api/favorites/' + encodeURIComponent(productId), { method: 'DELETE' });
  }

  // --- State ---

  let currentUser = null;
  let favoriteSet = new Set();

  // --- UI rendering ---

  function renderLoggedIn(user) {
    const authArea = document.getElementById('auth-area');
    if (!authArea) return;
    authArea.innerHTML =
      '<span class="auth-user-name">' + escapeHtml(user.name || user.email || '사용자') + '</span>' +
      '<button class="auth-btn auth-btn--logout" id="logout-btn">로그아웃</button>';
    document.getElementById('logout-btn')?.addEventListener('click', handleLogout);
  }

  function renderLoggedOut() {
    currentUser = null;
    favoriteSet.clear();
    const authArea = document.getElementById('auth-area');
    if (!authArea) return;
    const returnTo = encodeURIComponent(window.location.pathname);
    authArea.innerHTML =
      '<div class="auth-login-dropdown">' +
        '<button class="auth-btn auth-btn--login" id="login-toggle">로그인</button>' +
        '<div class="auth-dropdown-menu" id="login-menu" hidden>' +
          '<a href="' + API_BASE + '/auth/google?return_to=' + returnTo + '" class="auth-dropdown-item">Google</a>' +
          '<a href="' + API_BASE + '/auth/naver?return_to=' + returnTo + '" class="auth-dropdown-item">Naver</a>' +
          '<a href="' + API_BASE + '/auth/kakao?return_to=' + returnTo + '" class="auth-dropdown-item">Kakao</a>' +
        '</div>' +
      '</div>';
    document.getElementById('login-toggle')?.addEventListener('click', function () {
      var menu = document.getElementById('login-menu');
      if (menu) menu.hidden = !menu.hidden;
    });
    updateAllFavoriteButtons();
  }

  function updateAllFavoriteButtons() {
    document.querySelectorAll('[data-favorite-btn]').forEach(function (btn) {
      var pid = btn.getAttribute('data-product-id');
      btn.classList.toggle('favorite--active', favoriteSet.has(pid));
    });
    // Show/hide favorites tab indicator
    var favTab = document.querySelector('[data-category="favorites"]');
    if (favTab) {
      favTab.style.display = favoriteSet.size > 0 || currentUser ? '' : 'none';
    }
  }

  async function handleFavoriteClick(e) {
    var btn = e.currentTarget;
    var pid = btn.getAttribute('data-product-id');
    if (!currentUser) {
      // Prompt login
      var loginBtn = document.getElementById('login-toggle');
      if (loginBtn) loginBtn.click();
      return;
    }
    if (favoriteSet.has(pid)) {
      favoriteSet.delete(pid);
      btn.classList.remove('favorite--active');
      await removeFavorite(pid);
    } else {
      favoriteSet.add(pid);
      btn.classList.add('favorite--active');
      await addFavorite(pid);
    }
    updateAllFavoriteButtons();
  }

  function handleLogout() {
    clearToken();
    currentUser = null;
    favoriteSet.clear();
    renderLoggedOut();
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // --- Favorites tab filter ---

  function setupFavoritesFilter() {
    var favTab = document.querySelector('[data-category="favorites"]');
    if (!favTab) return;
    favTab.addEventListener('click', function () {
      document.querySelectorAll('.product-card').forEach(function (card) {
        var pid = card.getAttribute('data-product-id');
        card.style.display = favoriteSet.has(pid) ? '' : 'none';
      });
    });
  }

  // --- Init ---

  async function init() {
    checkHashToken();

    // Inject favorite buttons into product cards
    document.querySelectorAll('.product-card[data-product-id]').forEach(function (card) {
      var pid = card.getAttribute('data-product-id');
      var btn = document.createElement('button');
      btn.className = 'favorite-btn';
      btn.setAttribute('data-favorite-btn', '');
      btn.setAttribute('data-product-id', pid);
      btn.setAttribute('aria-label', '관심 목록에 추가');
      btn.textContent = '\u2661'; // heart outline
      btn.addEventListener('click', handleFavoriteClick);
      card.querySelector('.product-card__body')?.prepend(btn);
    });

    setupFavoritesFilter();

    var token = getToken();
    if (!token) {
      renderLoggedOut();
      return;
    }

    var user = await fetchMe();
    if (!user) {
      renderLoggedOut();
      return;
    }

    currentUser = user;
    renderLoggedIn(user);

    var favs = await fetchFavorites();
    favoriteSet = new Set(favs);
    updateAllFavoriteButtons();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
```

- [ ] **Step 2: 커밋**

```bash
git add js/auth.js
git commit -m "feat: add auth.js for frontend OAuth + favorites integration"
```

---

### Task 14: build_static_site.py에 auth.js 주입

**Files:**
- Modify: `scripts/build_static_site.py`

auth.js `<script>` 태그를 site.js 바로 뒤에 주입해야 한다. 수정 대상은 3곳:

- [ ] **Step 1: index.html 생성 부분 (약 837줄)**

`<script src="js/site.js" defer></script>` 줄 뒤에:
```python
  <script src="js/auth.js" defer></script>
```

- [ ] **Step 2: products/*.html 생성 부분 (약 1086줄)**

`<script src="../js/site.js" defer></script>` 줄 뒤에:
```python
  <script src="../js/auth.js" defer></script>
```

- [ ] **Step 3: resources.html 생성 부분 (약 1169줄)**

`<script src="js/site.js" defer></script>` 줄 뒤에:
```python
  <script src="js/auth.js" defer></script>
```

- [ ] **Step 4: index.html에 auth-area + favorites 탭용 HTML 삽입**

`build_index_page()` 함수 내 헤더 영역에 `<div id="auth-area"></div>` 추가.
카테고리 탭 목록에 `<button data-category="favorites" style="display:none">관심 목록</button>` 추가.

- [ ] **Step 5: 제품 카드에 data-product-id 속성 추가**

`createProductCard()` 등 카드 생성 부분에서 `.product-card` div에 `data-product-id="{product_id}"` 속성이 이미 있는지 확인하고, 없으면 추가.

- [ ] **Step 6: auth.js를 빌드 산출물 복사 목록에 추가**

`js/auth.js`가 dist/ 복사 시 포함되도록 파일 복사 로직에 추가.

- [ ] **Step 7: 전체 빌드 테스트**

```bash
python scripts/build_static_site.py --output dist
grep 'auth.js' dist/index.html && echo "OK: auth.js injected in index"
grep 'auth.js' dist/products/nikon-z9.html && echo "OK: auth.js injected in product page"
grep 'auth-area' dist/index.html && echo "OK: auth-area div present"
```

- [ ] **Step 8: 커밋**

```bash
git add scripts/build_static_site.py
git commit -m "feat: inject auth.js script tag into generated HTML pages"
```

---

## Chunk 8: 통합 테스트 + 최종 점검

### Task 15: 전체 테스트 실행

- [ ] **Step 1: 전체 테스트 스위트 실행**

```bash
cd /home/cantabile/Works/nikon_value
python -m pytest tests/ -v --tb=short
```

Expected: All tests pass

- [ ] **Step 2: 수동 통합 테스트 — 서버 시작**

```bash
# .env 파일 생성 (OAuth 키는 사전에 준비)
cp server/.env.example server/.env
# JWT_SECRET_KEY 설정
python -c "import secrets; print(secrets.token_urlsafe(32))"
# 위 값을 server/.env의 JWT_SECRET_KEY에 설정

# 서버 시작 (SSL 없이 로컬 테스트)
python -m uvicorn server.main:app --port 8000 --reload
```

- [ ] **Step 3: Health 확인**

```bash
curl http://localhost:8000/health
# {"status":"healthy","db":"ok","catalog_loaded":true,...}
```

- [ ] **Step 4: 커밋**

```bash
git add -A
git commit -m "chore: finalize auth + favorites implementation"
```

---

## 사전 필수 작업 (OAuth 앱 등록)

구현과 병행하여 진행해야 하는 작업:

1. **Google Cloud Console**: OAuth 2.0 클라이언트 ID 생성, 콜백 URL 등록
2. **Naver Developers**: 애플리케이션 등록, 콜백 URL 등록
3. **Kakao Developers**: 애플리케이션 등록, 콜백 URL 등록

각 제공자에서 `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` 등을 발급받아 `server/.env`에 기입.

> OAuth 콜백 URL은 기본 HTTPS URL 기준으로 등록한다. 별도 포트 포워딩은 운영 환경 설정에서 관리한다.
