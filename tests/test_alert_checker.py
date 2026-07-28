from __future__ import annotations

import pytest

from server import alerts, catalog, telegram
from server.alerts import check_price_alerts


async def _setup_alert(db, *, pid="g-checker", product_id="nikon-z9", target=1000.0):
    await db.execute(
        "INSERT OR IGNORE INTO users (provider, provider_id, name) VALUES (?, ?, ?)",
        ("google", pid, "Checker"),
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
    """알림 채널이 연결된 상황을 흉내 내는 발송 mock."""
    sent = []

    async def fake_send(user_id, subject, body):
        sent.append({"user_id": user_id, "subject": subject, "body": body})
        return result

    monkeypatch.setattr(alerts, "send_price_alert", fake_send)
    return sent


def _mock_telegram(monkeypatch, ok=True):
    """텔레그램 API 호출만 가로챈다(notify → telegram 경로는 실제 코드로 검증).

    실제 HTTP 요청은 절대 발생하지 않는다.
    """
    monkeypatch.setattr(telegram, "TELEGRAM_BOT_TOKEN", "123456:test-token")
    delivered = []

    async def fake_send_message(chat_id, text):
        delivered.append({"chat_id": chat_id, "text": text})
        return ok

    monkeypatch.setattr(telegram, "send_message", fake_send_message)
    return delivered


async def _link_telegram(db, user_id, chat_id="70001"):
    await db.execute(
        "UPDATE users SET telegram_chat_id = ?, telegram_linked_at = datetime('now') WHERE id = ?",
        (chat_id, user_id),
    )
    await db.commit()


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
    assert sent[0]["user_id"] == uid
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
async def test_default_stub_keeps_alert_pending_until_channel_lands(db, monkeypatch):
    """봇 토큰 미설정(기본 상태): 목표가에 도달해도 알림이 소비되지 않고
    대기 상태로 남아, 채널이 구성되면 그때 발송된다."""
    uid = await _setup_alert(db, pid="g-pending", target=1000.0)

    _set_catalog(monkeypatch, {"nikon-z9": 900.0})
    assert await check_price_alerts() == 0
    assert await _triggered_value(db, uid) == 0


# --- 텔레그램 채널 연동 후 실제 발송 경로 -----------------------------------


@pytest.mark.asyncio
async def test_user_without_telegram_stays_pending(db, monkeypatch):
    """봇은 설정됐지만 사용자가 텔레그램을 연동하지 않은 경우 → 대기 유지."""
    uid = await _setup_alert(db, pid="g-nolink", target=1000.0)
    delivered = _mock_telegram(monkeypatch)

    _set_catalog(monkeypatch, {"nikon-z9": 900.0})
    assert await check_price_alerts() == 0
    assert delivered == []
    assert await _triggered_value(db, uid) == 0


@pytest.mark.asyncio
async def test_pending_alert_is_delivered_when_user_links_telegram(db, monkeypatch):
    """이 프로젝트의 핵심 설계: 발송 성공 시에만 triggered를 갱신하므로,
    연동 전에 목표가에 도달한 알림도 소실되지 않고 연동 직후 발송된다."""
    uid = await _setup_alert(db, pid="g-latelink", target=1000.0)
    delivered = _mock_telegram(monkeypatch)
    _set_catalog(monkeypatch, {"nikon-z9": 900.0})

    # 1) 미연동 상태로 두 주기 → 계속 대기
    assert await check_price_alerts() == 0
    assert await check_price_alerts() == 0
    assert await _triggered_value(db, uid) == 0

    # 2) 사용자가 텔레그램을 연동 → 다음 주기에 대기 중이던 알림이 발송된다
    await _link_telegram(db, uid, chat_id="70002")
    assert await check_price_alerts() == 1
    assert len(delivered) == 1
    assert delivered[0]["chat_id"] == "70002"
    assert "이름-nikon-z9" in delivered[0]["text"]
    assert "$900.00" in delivered[0]["text"]
    assert await _triggered_value(db, uid) == 1

    # 3) 중복 발송 없음
    assert await check_price_alerts() == 0
    assert len(delivered) == 1


@pytest.mark.asyncio
async def test_telegram_api_failure_keeps_alert_pending(db, monkeypatch):
    """텔레그램 장애로 발송이 실패하면 triggered를 올리지 않아 다음 주기에 재시도된다."""
    uid = await _setup_alert(db, pid="g-tgfail", target=1000.0)
    delivered = _mock_telegram(monkeypatch, ok=False)
    await _link_telegram(db, uid, chat_id="70003")
    _set_catalog(monkeypatch, {"nikon-z9": 900.0})

    assert await check_price_alerts() == 0
    assert len(delivered) == 1
    assert await _triggered_value(db, uid) == 0

    assert await check_price_alerts() == 0
    assert len(delivered) == 2
    assert await _triggered_value(db, uid) == 0


@pytest.mark.asyncio
async def test_rearm_still_works_over_the_telegram_channel(db, monkeypatch):
    """가격 회복 → 재무장 → 재하락 시 다시 발송되는 규칙이 실제 채널에서도 유지된다."""
    uid = await _setup_alert(db, pid="g-tgrearm", target=1000.0)
    delivered = _mock_telegram(monkeypatch)
    await _link_telegram(db, uid, chat_id="70004")

    _set_catalog(monkeypatch, {"nikon-z9": 950.0})
    assert await check_price_alerts() == 1
    assert await _triggered_value(db, uid) == 1

    _set_catalog(monkeypatch, {"nikon-z9": 1100.0})
    assert await check_price_alerts() == 0
    assert await _triggered_value(db, uid) == 0

    _set_catalog(monkeypatch, {"nikon-z9": 980.0})
    assert await check_price_alerts() == 1
    assert len(delivered) == 2


@pytest.mark.asyncio
async def test_unlinking_telegram_puts_alerts_back_to_pending(db, monkeypatch):
    """연동을 해제하면 이후 알림은 발송되지 않고 대기 상태로 남는다."""
    uid = await _setup_alert(db, pid="g-tgunlink", target=1000.0)
    delivered = _mock_telegram(monkeypatch)
    await _link_telegram(db, uid, chat_id="70005")

    _set_catalog(monkeypatch, {"nikon-z9": 950.0})
    assert await check_price_alerts() == 1

    # 재무장시킨 뒤 연동 해제
    _set_catalog(monkeypatch, {"nikon-z9": 1100.0})
    await check_price_alerts()
    await telegram.unlink_user(uid)

    _set_catalog(monkeypatch, {"nikon-z9": 940.0})
    assert await check_price_alerts() == 0
    assert len(delivered) == 1
    assert await _triggered_value(db, uid) == 0
