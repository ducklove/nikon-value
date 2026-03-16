from __future__ import annotations

from server.auth.routes import build_frontend_redirect_url, normalize_return_to


def test_normalize_return_to_strips_frontend_prefix():
    assert normalize_return_to("/nikon-value/") == "/"
    assert normalize_return_to("/nikon-value/products/nikon-zf.html") == "/products/nikon-zf.html"


def test_normalize_return_to_accepts_relative_paths_and_query():
    assert normalize_return_to("products/nikon-zf.html") == "/products/nikon-zf.html"
    assert normalize_return_to("/nikon-value/products/nikon-zf.html?currency=krw") == "/products/nikon-zf.html?currency=krw"


def test_normalize_return_to_rejects_external_urls():
    assert normalize_return_to("https://evil.example/steal") == "/"


def test_build_frontend_redirect_url_avoids_duplicate_repo_path():
    assert (
        build_frontend_redirect_url("/nikon-value/products/nikon-zf.html", "abc123")
        == "https://ducklove.github.io/nikon-value/products/nikon-zf.html#token=abc123"
    )
