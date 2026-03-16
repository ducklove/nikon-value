from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from server import catalog
from server.api import favorites, health, users
from server.config import DB_PATH, FRONTEND_URL
from server.database import close_db, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    await init_db()
    await catalog.load_catalog()
    catalog.start_refresh()
    health.set_start_time()
    yield
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
app.include_router(users.router)
app.include_router(favorites.router)
