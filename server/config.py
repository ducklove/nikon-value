from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

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

API_BASE_URL = _env("API_BASE_URL", "https://cantabile.tplinkdns.com:3380")
FRONTEND_URL = _env("FRONTEND_URL", "https://ducklove.github.io/nikon-value")
_frontend = urlsplit(FRONTEND_URL)
FRONTEND_ORIGIN = (
    f"{_frontend.scheme}://{_frontend.netloc}"
    if _frontend.scheme and _frontend.netloc
    else FRONTEND_URL
)
CATALOG_URL = _env(
    "CATALOG_URL",
    "https://ducklove.github.io/nikon-value/data/catalog.json",
)

SSL_CERTFILE = _env("SSL_CERTFILE", "")
SSL_KEYFILE = _env("SSL_KEYFILE", "")

DB_PATH = _env("DB_PATH", str(Path(__file__).parent / "data" / "nikon_api.db"))

FAVORITES_MAX = 50
CATALOG_REFRESH_SECONDS = 3600

OAUTH_STATE_MAX_AGE = 300  # 5 minutes
