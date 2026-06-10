from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server.config import JWT_ALGORITHM, JWT_EXPIRE_DAYS, JWT_SECRET_KEY

_bearer = HTTPBearer(auto_error=False)


def create_token(
    user_id: int, provider: str, expire_days: int | None = None
) -> str:
    days = expire_days if expire_days is not None else JWT_EXPIRE_DAYS
    exp = datetime.now(UTC) + timedelta(days=days)
    return jwt.encode(
        {"sub": str(user_id), "provider": provider, "exp": exp},
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def decode_token(token: str) -> dict:
    payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    # Convert sub back to int for consistency with create_token
    if "sub" in payload:
        payload["sub"] = int(payload["sub"])
    return payload


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
        ) from None
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "유효하지 않은 토큰입니다"},
        ) from None
