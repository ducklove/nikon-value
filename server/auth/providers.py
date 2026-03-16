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
