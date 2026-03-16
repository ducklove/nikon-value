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
