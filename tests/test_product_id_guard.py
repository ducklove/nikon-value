from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from server.api.favorites import PRODUCT_ID_RE
from server.auth.jwt import create_token
from server.database import get_db

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "products.yaml"


async def _setup_user(pid="g-guard"):
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (provider, provider_id, name) VALUES (?, ?, ?)",
            ("google", pid, "Guard Tester"),
        )
        await db.commit()
        cursor = await db.execute("SELECT id FROM users WHERE provider_id = ?", (pid,))
        return (await cursor.fetchone())["id"]


def _auth(user_id):
    token = create_token(user_id=user_id, provider="google")
    return {"Authorization": f"Bearer {token}"}


def test_all_catalog_ids_match_guard_pattern():
    """가드 정규식이 실제 카탈로그 ID를 절대 차단하지 않음을 보장한다."""
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    bad = [
        product["id"]
        for category in config["categories"]
        for product in category["products"]
        if not PRODUCT_ID_RE.fullmatch(product["id"])
    ]
    assert bad == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_id",
    [
        "UPPERCASE-ID",
        "has space",
        "semi;colon",
        "-leading-dash",
        "x" * 101,
        "한글아이디",
    ],
)
async def test_add_favorite_rejects_malformed_product_id(client, bad_id):
    uid = await _setup_user("g-guard-reject")
    resp = await client.put(f"/api/favorites/{bad_id}", headers=_auth(uid))
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


@pytest.mark.asyncio
async def test_add_favorite_accepts_wellformed_product_id(client):
    uid = await _setup_user("g-guard-accept")
    resp = await client.put("/api/favorites/nikon-z9", headers=_auth(uid))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
