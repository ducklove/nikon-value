from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


JWT_SECRET_KEY = _env("JWT_SECRET_KEY")
if len(JWT_SECRET_KEY) < 32:
    raise RuntimeError(
        "JWT_SECRET_KEY must be a random secret of at least 32 characters. "
        'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
    )
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = int(_env("JWT_EXPIRE_DAYS", "7"))

GOOGLE_CLIENT_ID = _env("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = _env("GOOGLE_CLIENT_SECRET")
NAVER_CLIENT_ID = _env("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = _env("NAVER_CLIENT_SECRET")
KAKAO_CLIENT_ID = _env("KAKAO_CLIENT_ID")
KAKAO_CLIENT_SECRET = _env("KAKAO_CLIENT_SECRET")

API_BASE_URL = _env("API_BASE_URL", "https://cantabile.tplinkdns.com")
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

DB_PATH = _env("DB_PATH", str(Path(__file__).parent / "data" / "nikon_api.db"))

# 텔레그램 알림 채널. 미설정이어도 서버는 정상 기동하며, 발송은 조용히 False를
# 반환하고 로그만 남긴다(= 알림이 대기 상태로 남아 다음 주기에 재시도된다).
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
# 안내 문구/딥링크(https://t.me/<username>)용. 선택 사항이며 토큰과 달리 공개 값이다.
TELEGRAM_BOT_USERNAME = _env("TELEGRAM_BOT_USERNAME").lstrip("@")
TELEGRAM_API_BASE = _env("TELEGRAM_API_BASE", "https://api.telegram.org")

FAVORITES_MAX = 50
ALERTS_MAX = 50
CATALOG_REFRESH_SECONDS = 3600

OAUTH_STATE_MAX_AGE = 300  # 5 minutes

TELEGRAM_LINK_CODE_TTL = 600  # 연동 코드 유효 시간: 10분
TELEGRAM_POLL_TIMEOUT = 25  # getUpdates long polling 대기(초)
TELEGRAM_SEND_TIMEOUT = 10  # sendMessage 요청 타임아웃(초)
