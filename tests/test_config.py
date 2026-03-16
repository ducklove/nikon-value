from __future__ import annotations

from server.config import FRONTEND_ORIGIN, FRONTEND_URL


def test_frontend_origin_strips_path():
    assert FRONTEND_URL == "https://ducklove.github.io/nikon-value"
    assert FRONTEND_ORIGIN == "https://ducklove.github.io"
