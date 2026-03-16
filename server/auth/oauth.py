from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from server.config import JWT_SECRET_KEY, OAUTH_STATE_MAX_AGE


def create_state(return_to: str = "/") -> str:
    payload = {
        "ts": int(time.time()),
        "nonce": secrets.token_urlsafe(16),
        "return_to": return_to or "/",
    }
    data = json.dumps(payload, separators=(",", ":"))
    sig = _sign(data)
    raw = base64.urlsafe_b64encode(f"{data}|{sig}".encode()).decode()
    return raw


def verify_state(state: str) -> dict:
    try:
        decoded = base64.urlsafe_b64decode(state).decode()
        # Validate that we consumed the entire state string by re-encoding
        reencoded = base64.urlsafe_b64encode(decoded.encode()).decode()
        if reencoded != state:
            raise ValueError("Invalid state format")
    except Exception:
        raise ValueError("Invalid state format")

    # Split into data and signature
    try:
        data, sig = decoded.rsplit("|", 1)
    except ValueError:
        raise ValueError("Invalid state format")

    if not hmac.compare_digest(sig, _sign(data)):
        raise ValueError("Invalid state signature")

    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        raise ValueError("Invalid state data")

    age = time.time() - payload["ts"]
    if age > OAUTH_STATE_MAX_AGE:
        raise ValueError("State expired")

    return payload


def _sign(data: str) -> str:
    return hmac.new(
        JWT_SECRET_KEY.encode(), data.encode(), hashlib.sha256
    ).hexdigest()
