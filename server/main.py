from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
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
from server.rate_limit import TRUST_PROXY_HEADERS, client_ip, limiter

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

@app.get("/health/client-ip")
@limiter.limit("30/minute")
async def client_ip_probe(request: Request):
    """운영자가 자기 배포 형태(리버스 프록시 유무)를 확인하기 위한 진단용 엔드포인트.

    응답의 `client_ip` 가 rate limit 버킷을 가르는 실제 키다. 여러 회선에서 호출해도
    같은 값이 나온다면 모든 사용자가 한 버킷을 공유하고 있다는 뜻이다.
    판독법은 docs/deploy-api-server.md 의 "리버스 프록시와 클라이언트 IP" 참고.
    """
    return {
        "client_ip": client_ip(request),
        "peer_ip": request.client.host if request.client else None,
        "x_forwarded_for": request.headers.get("x-forwarded-for"),
        "x_real_ip": request.headers.get("x-real-ip"),
        "trust_proxy_headers": TRUST_PROXY_HEADERS,
    }


app.include_router(health.router)
app.include_router(users.router)
app.include_router(favorites.router)
app.include_router(alerts.router)
app.include_router(telegram_api.router)
app.include_router(auth_routes.router)
