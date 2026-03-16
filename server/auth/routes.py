from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from server.auth.jwt import create_token
from server.auth.oauth import create_state, verify_state
from server.auth.providers import PROVIDERS, get_nested, get_oauth_client
from server.config import FRONTEND_URL
from server.database import get_db
from server.rate_limit import limiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth")


@router.get("/{provider}")
@limiter.limit("5/minute")
async def oauth_start(provider: str, request: Request):
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")

    return_to = request.query_params.get("return_to", "/")
    state = create_state(return_to=return_to)

    cfg = PROVIDERS[provider]
    client = get_oauth_client(provider)
    url = client.create_authorization_url(cfg["authorize_url"], state=state, scope=cfg["scope"])
    return RedirectResponse(url[0])


@router.get("/{provider}/callback")
@limiter.limit("5/minute")
async def oauth_callback(provider: str, request: Request, code: str = "", state: str = ""):
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")

    try:
        state_data = verify_state(state)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid state: {e}")

    cfg = PROVIDERS[provider]
    client = get_oauth_client(provider)

    try:
        token = await client.fetch_token(cfg["token_url"], code=code)
    except Exception:
        logger.exception("Token exchange failed for %s", provider)
        raise HTTPException(status_code=502, detail="OAuth token exchange failed")

    try:
        resp = await client.get(cfg["userinfo_url"])
        resp.raise_for_status()
        userinfo = resp.json()
    except Exception:
        logger.exception("Userinfo fetch failed for %s", provider)
        raise HTTPException(status_code=502, detail="Failed to fetch user info")
    finally:
        await client.aclose()

    provider_id = str(get_nested(userinfo, cfg["id_field"]) or "")
    email = get_nested(userinfo, cfg["email_field"])
    name = get_nested(userinfo, cfg["name_field"])

    if not provider_id:
        raise HTTPException(status_code=502, detail="Could not get user ID from provider")

    async with get_db() as db:
        await db.execute(
            """INSERT INTO users (provider, provider_id, email, name)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(provider, provider_id) DO UPDATE SET
                 email = excluded.email,
                 name = excluded.name,
                 last_login = datetime('now')""",
            (provider, provider_id, email, name),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT id FROM users WHERE provider = ? AND provider_id = ?",
            (provider, provider_id),
        )
        user_id = (await cursor.fetchone())["id"]

    jwt_token = create_token(user_id=user_id, provider=provider)
    return_to = state_data.get("return_to", "/")
    redirect_url = f"{FRONTEND_URL}{return_to}#token={jwt_token}"
    return RedirectResponse(redirect_url)
