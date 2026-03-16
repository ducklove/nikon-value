from __future__ import annotations

import time

import pytest

from server.auth.oauth import create_state, verify_state


def test_create_and_verify_state():
    state = create_state(return_to="/products/nikon-z9.html")
    data = verify_state(state)
    assert data["return_to"] == "/products/nikon-z9.html"


def test_verify_tampered_state():
    state = create_state(return_to="/")
    with pytest.raises(ValueError, match="Invalid"):
        verify_state(state + "tampered")


def test_verify_expired_state(monkeypatch):
    state = create_state(return_to="/")
    # Simulate 6 minutes later (beyond 5 min max age)
    current_time = time.time()
    monkeypatch.setattr("server.auth.oauth.time.time", lambda: current_time + 360)
    with pytest.raises(ValueError, match="expired"):
        verify_state(state)


def test_state_without_return_to():
    state = create_state()
    data = verify_state(state)
    assert data["return_to"] == "/"
