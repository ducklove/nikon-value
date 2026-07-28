from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from server import catalog, telegram
from server.alerts import check_price_alerts
from server.api import alerts, favorites, health, users
from server.api import telegram as telegram_api
from server.auth import routes as auth_routes
from server.config import DB_PATH, FRONTEND_ORIGIN
from server.database import close_db, init_db
from server.rate_limit import limiter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    await init_db()
    await catalog.load_catalog()
    if catalog.is_loaded():
        await check_price_alerts()
    # 카탈로그가 1시간마다 갱신될 때마다 가격 알림을 점검한다.
    catalog.start_refresh(on_refresh=check_price_alerts)
    # 봇 토큰이 설정된 경우에만 텔레그램 수신 폴링 루프를 띄운다(미설정이면 no-op).
    telegram.start_polling()
    health.set_start_time()
    yield
    telegram.stop_polling()
    catalog.stop_refresh()
    await close_db()


app = FastAPI(title="Nikon Value API", lifespan=lifespan)
app.state.limiter = limiter

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=False,
    allow_methods=["GET", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization"],
)

app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(health.router)
app.include_router(users.router)
app.include_router(favorites.router)
app.include_router(alerts.router)
app.include_router(telegram_api.router)
app.include_router(auth_routes.router)
