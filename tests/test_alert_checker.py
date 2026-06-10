from __future__ import annotations

import pytest

from server import alerts, catalog
from server.alerts import check_price_alerts


async def _setup_alert(db, *, pid="g-checker", product_id="nikon-z9", target=1000.0, email="user@example.com"):
    await db.execute(
        "INSERT OR IGNORE INTO users (provider, provider_id, name, email) VALUES (?, ?, ?, ?)",
        ("google", pid, "Checker", email),
    )
    await db.commit()
    cursor = await db.execute("SELECT id FROM users WHERE provider_id = ?", (pid,))
    user_id = (await cursor.fetchone())["id"]
    await db.execute(
        "INSERT INTO price_alerts (user_id, product_id, target_price) VALUES (?, ?, ?)",
        (user_id, product_id, target),
    )
    await db.commit()
    return user_id


def _set_catalog(monkeypatch, medians: dict[str, float | None]):
    monkeypatch.setattr(catalog, "_loaded", True)
    monkeypatch.setattr(catalog, "_product_ids", set(medians))
    monkeypatch.setattr(catalog, "_product_medians", dict(medians))
    monkeypatch.setattr(catalog, "_product_names", {k: f"이름-{k}" for k in medians})


def _mock_send(monkeypatch, result=True):
    sent = []

    async def fake_send(to, subject, body):
        sent.append({"to": to, "subject": subject, "body": body})
        return result

    monkeypatch.setattr(alerts, "send_email", fake_send)
    return sent


async def _triggered_value(db, user_id):
    cursor = await db.execute("SELECT triggered FROM price_alerts WHERE user_id = ?", (user_id,))
    return (await cursor.fetchone())["triggered"]


@pytest.mark.asyncio
async def test_checker_noop_when_catalog_not_loaded(db, monkeypatch):
    monkeypatch.setattr(catalog, "_loaded", False)
    assert await check_price_alerts() == 0


@pytest.mark.asyncio
async def test_checker_sends_once_and_rearms(db, monkeypatch):
    uid = await _setup_alert(db, pid="g-rearm", target=1000.0)
    sent = _mock_send(monkeypatch)

    # 1) 목표가 도달 → 1회 발송 + triggered=1
    _set_catalog(monkeypatch, {"nikon-z9": 950.0})
    assert await check_price_alerts() == 1
    assert len(sent) == 1
    assert sent[0]["to"] == "user@example.com"
    assert "이름-nikon-z9" in sent[0]["subject"]
    assert "$950.00" in sent[0]["body"]
    assert "nikon-z9.html" in sent[0]["body"]
    assert await _triggered_value(db, uid) == 1

    # 2) 같은 상태에서 재점검 → 중복 발송 없음
    assert await check_price_alerts() == 0
    assert len(sent) == 1

    # 3) 가격 회복 → 재무장
    _set_catalog(monkeypatch, {"nikon-z9": 1100.0})
    assert await check_price_alerts() == 0
    assert await _triggered_value(db, uid) == 0

    # 4) 다시 하락 → 재발송
    _set_catalog(monkeypatch, {"nikon-z9": 980.0})
    assert await check_price_alerts() == 1
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_checker_skips_when_above_target_or_median_missing(db, monkeypatch):
    uid = await _setup_alert(db, pid="g-skip", target=1000.0)
    sent = _mock_send(monkeypatch)

    _set_catalog(monkeypatch, {"nikon-z9": 1500.0})
    assert await check_price_alerts() == 0

    _set_catalog(monkeypatch, {"nikon-z9": None})
    assert await check_price_alerts() == 0

    assert sent == []
    assert await _triggered_value(db, uid) == 0


@pytest.mark.asyncio
async def test_checker_retries_when_send_fails(db, monkeypatch):
    uid = await _setup_alert(db, pid="g-retry", target=1000.0)
    sent = _mock_send(monkeypatch, result=False)

    _set_catalog(monkeypatch, {"nikon-z9": 900.0})
    # 발송 실패 → triggered 유지(0) → 다음 주기에 재시도된다
    assert await check_price_alerts() == 0
    assert len(sent) == 1
    assert await _triggered_value(db, uid) == 0

    assert await check_price_alerts() == 0
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_checker_skips_user_without_email(db, monkeypatch):
    uid = await _setup_alert(db, pid="g-noemail", email=None)
    sent = _mock_send(monkeypatch)

    _set_catalog(monkeypatch, {"nikon-z9": 900.0})
    assert await check_price_alerts() == 0
    assert sent == []
    assert await _triggered_value(db, uid) == 0
