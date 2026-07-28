from __future__ import annotations

from server.config import FRONTEND_ORIGIN, FRONTEND_URL, TRUSTED_PROXY_IPS


def test_frontend_origin_strips_path():
    assert FRONTEND_URL == "https://ducklove.github.io/nikon-value"
    assert FRONTEND_ORIGIN == "https://ducklove.github.io"


def test_trusted_proxy_ips_defaults_to_empty():
    """미설정이 기본이어야 한다. 기본값을 채우면 프록시 없는 배포가 위조에 노출된다."""
    assert TRUSTED_PROXY_IPS == ""


def test_server_starts_without_proxy_env(monkeypatch):
    """프록시 관련 환경변수가 없어도 config 가 예외 없이 로드되어야 한다."""
    import importlib

    monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)
    module = importlib.import_module("server.config")
    importlib.reload(module)
    assert module.TRUSTED_PROXY_IPS == ""
