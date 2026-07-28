"""텔레그램 연동 코드 발급·검증·만료·재사용 차단과 연동 API 테스트.

텔레그램 API는 절대 실제로 호출하지 않는다 — send_message를 mock으로 대체한다.
"""

from __future__ import annotations

import pytest

from server import telegram
from server.auth.jwt import create_token
from server.database import get_db

BOT_TOKEN = "123456:test-bot-token-should-never-leak"


@pytest.fixture
def configured(monkeypatch):
    """봇 토큰이 설정된 상태를 흉내 낸다(실제 호출은 하지 않는다)."""
    monkeypatch.setattr(telegram, "TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(telegram, "TELEGRAM_BOT_USERNAME", "nikon_value_bot")


@pytest.fixture
def sent(monkeypatch):
    """봇이 사용자에게 보낸 답장을 기록하는 mock."""
    messages = []

    async def fake_send(chat_id, text):
        messages.append({"chat_id": chat_id, "text": text})
        return True

    monkeypatch.setattr(telegram, "send_message", fake_send)
    return messages


async def _create_user(db, provider_id="g-tg"):
    await db.execute(
        "INSERT INTO users (provider, provider_id, name) VALUES (?, ?, ?)",
        ("google", provider_id, "TG Tester"),
    )
    await db.commit()
    cursor = await db.execute("SELECT id FROM users WHERE provider_id = ?", (provider_id,))
    return (await cursor.fetchone())["id"]


async def _chat_id_of(db, user_id):
    cursor = await db.execute("SELECT telegram_chat_id FROM users WHERE id = ?", (user_id,))
    return (await cursor.fetchone())["telegram_chat_id"]


async def _expire_codes(db):
    await db.execute("UPDATE telegram_link_codes SET expires_at = datetime('now', '-1 second')")
    await db.commit()


# --- 코드 생성/정규화 -------------------------------------------------------


def test_generated_code_is_unguessable_format():
    codes = {telegram.generate_code() for _ in range(200)}
    assert len(codes) > 190  # 사실상 중복이 없어야 한다
    for code in codes:
        assert len(code) == telegram.CODE_LENGTH
        assert set(code) <= set(telegram.CODE_ALPHABET)
        # 혼동하기 쉬운 글자는 알파벳에서 제외돼 있다.
        assert not (set(code) & set("O0I1"))


def test_normalize_code_accepts_user_typos_and_rejects_junk():
    assert telegram.normalize_code(" abcd2345 ") == "ABCD2345"
    assert telegram.normalize_code("ABCD-2345") == "ABCD2345"
    assert telegram.normalize_code("ABCD234") is None  # 길이 부족
    assert telegram.normalize_code("ABCD23450") is None  # 길이 초과
    assert telegram.normalize_code("ABCD_234") is None  # 허용되지 않는 문자
    assert telegram.normalize_code("ABCD2340") is None  # 알파벳에 없는 0
    assert telegram.normalize_code("") is None
    assert telegram.normalize_code(None) is None


# --- 발급 / 검증 ------------------------------------------------------------


@pytest.mark.asyncio
async def test_code_is_stored_hashed_not_plaintext(db):
    user_id = await _create_user(db)
    code, ttl = await telegram.issue_link_code(user_id)
    assert ttl == 600

    cursor = await db.execute("SELECT code_hash, user_id FROM telegram_link_codes")
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["user_id"] == user_id
    assert rows[0]["code_hash"] != code
    assert code not in rows[0]["code_hash"]


@pytest.mark.asyncio
async def test_valid_code_links_chat_id(db):
    user_id = await _create_user(db)
    code, _ = await telegram.issue_link_code(user_id)

    assert await telegram.consume_link_code(code, "555001") == user_id
    assert await _chat_id_of(db, user_id) == "555001"
    assert await telegram.get_chat_id(user_id) == "555001"

    status = await telegram.link_status(user_id)
    assert status["linked"] is True
    assert status["linked_at"] is not None
    # chat_id 원문은 상태 응답에 포함하지 않는다.
    assert "555001" not in str(status)


@pytest.mark.asyncio
async def test_code_is_single_use(db):
    user_id = await _create_user(db)
    code, _ = await telegram.issue_link_code(user_id)

    assert await telegram.consume_link_code(code, "555002") == user_id
    # 같은 코드를 다른 chat_id로 재사용할 수 없다.
    assert await telegram.consume_link_code(code, "999999") is None
    assert await _chat_id_of(db, user_id) == "555002"


@pytest.mark.asyncio
async def test_expired_code_is_rejected(db):
    user_id = await _create_user(db)
    code, _ = await telegram.issue_link_code(user_id)
    await _expire_codes(db)

    assert await telegram.consume_link_code(code, "555003") is None
    assert await _chat_id_of(db, user_id) is None
    # 만료된 행은 정리된다.
    cursor = await db.execute("SELECT COUNT(*) AS cnt FROM telegram_link_codes")
    assert (await cursor.fetchone())["cnt"] == 0


@pytest.mark.asyncio
async def test_unknown_or_malformed_code_is_rejected(db):
    user_id = await _create_user(db)
    await telegram.issue_link_code(user_id)

    assert await telegram.consume_link_code("ZZZZZZZZ", "555004") is None
    assert await telegram.consume_link_code("not-a-code", "555004") is None
    assert await telegram.consume_link_code("", "555004") is None
    assert await _chat_id_of(db, user_id) is None


@pytest.mark.asyncio
async def test_issuing_new_code_invalidates_previous(db):
    user_id = await _create_user(db)
    first, _ = await telegram.issue_link_code(user_id)
    second, _ = await telegram.issue_link_code(user_id)

    assert await telegram.consume_link_code(first, "555005") is None
    assert await telegram.consume_link_code(second, "555005") == user_id


@pytest.mark.asyncio
async def test_chat_id_moves_to_the_latest_account(db):
    user_a = await _create_user(db, "g-tg-a")
    user_b = await _create_user(db, "g-tg-b")

    code_a, _ = await telegram.issue_link_code(user_a)
    await telegram.consume_link_code(code_a, "555006")
    code_b, _ = await telegram.issue_link_code(user_b)
    await telegram.consume_link_code(code_b, "555006")

    # 텔레그램 계정 하나는 사이트 계정 하나에만 연결된다.
    assert await _chat_id_of(db, user_a) is None
    assert await _chat_id_of(db, user_b) == "555006"


@pytest.mark.asyncio
async def test_unlink_user_clears_link_and_pending_codes(db):
    user_id = await _create_user(db)
    code, _ = await telegram.issue_link_code(user_id)
    await telegram.consume_link_code(code, "555007")

    await telegram.issue_link_code(user_id)  # 대기 중 코드
    assert await telegram.unlink_user(user_id) is True
    assert await _chat_id_of(db, user_id) is None
    cursor = await db.execute("SELECT COUNT(*) AS cnt FROM telegram_link_codes WHERE user_id = ?", (user_id,))
    assert (await cursor.fetchone())["cnt"] == 0

    # 이미 해제된 상태에서 다시 호출해도 안전하다.
    assert await telegram.unlink_user(user_id) is False


@pytest.mark.asyncio
async def test_deleting_account_removes_link_codes(db):
    user_id = await _create_user(db)
    await telegram.issue_link_code(user_id)
    await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await db.commit()
    cursor = await db.execute("SELECT COUNT(*) AS cnt FROM telegram_link_codes")
    assert (await cursor.fetchone())["cnt"] == 0


# --- 봇 업데이트 처리(신뢰할 수 없는 입력) ----------------------------------


@pytest.mark.asyncio
async def test_update_with_code_links_and_confirms(db, configured, sent):
    user_id = await _create_user(db)
    code, _ = await telegram.issue_link_code(user_id)

    await telegram.handle_update({"update_id": 1, "message": {"chat": {"id": 777}, "text": code}})

    assert await _chat_id_of(db, user_id) == "777"
    assert len(sent) == 1
    assert "연동이 완료" in sent[0]["text"]


@pytest.mark.asyncio
async def test_update_accepts_start_command_with_code(db, configured, sent):
    user_id = await _create_user(db)
    code, _ = await telegram.issue_link_code(user_id)

    await telegram.handle_update(
        {"update_id": 2, "message": {"chat": {"id": 778}, "text": f"/start@nikon_value_bot {code.lower()}"}}
    )
    assert await _chat_id_of(db, user_id) == "778"


@pytest.mark.asyncio
async def test_update_with_bad_code_replies_help_and_links_nothing(db, configured, sent):
    user_id = await _create_user(db)
    await telegram.issue_link_code(user_id)

    await telegram.handle_update({"update_id": 3, "message": {"chat": {"id": 779}, "text": "ZZZZZZZZ"}})
    assert await _chat_id_of(db, user_id) is None
    assert "유효하지 않거나 만료" in sent[0]["text"]

    await telegram.handle_update({"update_id": 4, "message": {"chat": {"id": 779}, "text": "안녕하세요"}})
    assert "연동 코드를 발급" in sent[1]["text"]

    await telegram.handle_update({"update_id": 5, "message": {"chat": {"id": 779}, "text": "/start"}})
    assert "연동 코드를 발급" in sent[2]["text"]


@pytest.mark.asyncio
async def test_unlink_command_from_bot(db, configured, sent):
    user_id = await _create_user(db)
    code, _ = await telegram.issue_link_code(user_id)
    await telegram.consume_link_code(code, "780")

    await telegram.handle_update({"update_id": 6, "message": {"chat": {"id": 780}, "text": "/unlink"}})
    assert await _chat_id_of(db, user_id) is None
    assert "해제" in sent[0]["text"]

    await telegram.handle_update({"update_id": 7, "message": {"chat": {"id": 780}, "text": "/unlink"}})
    assert "연동된 계정이 없습니다" in sent[1]["text"]


@pytest.mark.asyncio
async def test_malformed_updates_are_ignored(db, configured, sent):
    malformed = [
        None,
        "문자열",
        {},
        {"message": None},
        {"message": {"chat": None, "text": "ABCD2345"}},
        {"message": {"chat": {"id": "777"}, "text": "ABCD2345"}},  # chat.id가 문자열
        {"message": {"chat": {"id": True}, "text": "ABCD2345"}},  # bool은 chat_id가 아니다
        {"message": {"chat": {"id": 777}}},  # 텍스트 없음(사진 등)
        {"message": {"chat": {"id": 777}, "text": 12345}},
    ]
    for update in malformed:
        await telegram.handle_update(update)
    assert sent == []


@pytest.mark.asyncio
async def test_absurdly_long_text_is_truncated_and_harmless(db, configured, sent):
    user_id = await _create_user(db)
    code, _ = await telegram.issue_link_code(user_id)
    await telegram.handle_update(
        {"update_id": 8, "message": {"chat": {"id": 781}, "text": code + "A" * 10000}}
    )
    # 코드 뒤에 쓰레기가 붙으면 형식 검사에서 걸러져 안내문만 나간다.
    assert await _chat_id_of(db, user_id) is None
    assert "연동 코드를 발급" in sent[0]["text"]
    # 원문을 그대로 되돌려주지 않는다(로그/응답 증폭 방지).
    assert len(sent[0]["text"]) < 1000


@pytest.mark.asyncio
async def test_poll_once_advances_offset_and_handles_updates(db, configured, monkeypatch):
    handled = []

    async def fake_fetch(offset):
        assert offset == 0
        return [
            {"update_id": 10, "message": {"chat": {"id": 1}, "text": "x"}},
            {"update_id": 11, "message": {"chat": {"id": 1}, "text": "y"}},
        ]

    async def fake_handle(update):
        handled.append(update)

    monkeypatch.setattr(telegram, "fetch_updates", fake_fetch)
    monkeypatch.setattr(telegram, "handle_update", fake_handle)

    assert await telegram.poll_once(0) == 12
    assert len(handled) == 2
    # offset이 DB에 저장돼 재기동 후 같은 메시지를 다시 처리하지 않는다.
    assert await telegram._load_offset() == 12


@pytest.mark.asyncio
async def test_poll_once_survives_a_failing_update(db, configured, monkeypatch):
    async def fake_fetch(offset):
        return [{"update_id": 20, "message": {"chat": {"id": 1}, "text": "x"}}]

    async def boom(update):
        raise RuntimeError("handler exploded")

    monkeypatch.setattr(telegram, "fetch_updates", fake_fetch)
    monkeypatch.setattr(telegram, "handle_update", boom)

    # 하나가 실패해도 offset은 전진해 같은 메시지에 갇히지 않는다.
    assert await telegram.poll_once(0) == 21


def test_start_polling_is_noop_without_token(monkeypatch):
    monkeypatch.setattr(telegram, "TELEGRAM_BOT_TOKEN", "")
    telegram.start_polling()  # 이벤트 루프가 없어도 예외 없이 통과해야 한다
    assert telegram._poll_task is None


# --- API --------------------------------------------------------------------


def _auth(user_id):
    return {"Authorization": f"Bearer {create_token(user_id=user_id, provider='google')}"}


async def _api_user(provider_id="g-tg-api"):
    async with get_db() as db:
        return await _create_user(db, provider_id)


@pytest.mark.asyncio
async def test_telegram_endpoints_require_auth(client):
    assert (await client.get("/api/me/telegram")).status_code == 401
    assert (await client.put("/api/me/telegram/link-code")).status_code == 401
    assert (await client.delete("/api/me/telegram")).status_code == 401


@pytest.mark.asyncio
async def test_status_reports_unconfigured_channel(client):
    uid = await _api_user("g-tg-status")
    resp = await client.get("/api/me/telegram", headers=_auth(uid))
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"configured": False, "linked": False, "linked_at": None, "bot_username": None}


@pytest.mark.asyncio
async def test_link_code_endpoint_unavailable_without_bot_token(client):
    uid = await _api_user("g-tg-noconf")
    resp = await client.put("/api/me/telegram/link-code", headers=_auth(uid))
    assert resp.status_code == 503
    assert resp.json()["error"] == "channel_unavailable"


@pytest.mark.asyncio
async def test_link_code_roundtrip_through_api(client, configured):
    uid = await _api_user("g-tg-flow")
    headers = _auth(uid)

    resp = await client.put("/api/me/telegram/link-code", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    code = body["code"]
    assert telegram.normalize_code(code) == code
    assert body["expires_in"] == 600
    assert body["bot_username"] == "nikon_value_bot"
    assert body["deep_link"] == f"https://t.me/nikon_value_bot?start={code}"
    # 봇 토큰은 어떤 응답에도 노출되지 않는다.
    assert BOT_TOKEN not in resp.text

    # 사용자가 봇에 코드를 보낸 상황
    assert await telegram.consume_link_code(code, "88001") == uid

    status = (await client.get("/api/me/telegram", headers=headers)).json()
    assert status["configured"] is True
    assert status["linked"] is True

    resp = await client.delete("/api/me/telegram", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert (await client.get("/api/me/telegram", headers=headers)).json()["linked"] is False
