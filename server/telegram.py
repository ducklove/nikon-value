"""텔레그램 알림 채널.

역할이 셋이다.

1) 발송: 사용자에게 연결된 chat_id로 sendMessage를 호출한다(notify.py에서 사용).
2) 연동: 로그인한 사용자가 발급받은 일회용 코드를 봇에게 보내면 chat_id를 계정에 연결한다.
3) 수신: getUpdates long polling 백그라운드 루프로 봇에게 온 메시지를 처리한다.

수신 방식으로 웹훅이 아니라 폴링을 택한 이유는 운영 환경이 가정용 회선의
라즈베리파이이기 때문이다. 웹훅은 텔레그램이 서버로 직접 들어오는(inbound)
공인 HTTPS 엔드포인트를 요구하지만, 이 서버는 동적 DNS(tplinkdns.com) 뒤에 있어
IP 변동·포트포워딩·인증서 갱신 중 어느 하나만 어긋나도 알림이 조용히 끊긴다.
폴링은 아웃바운드 연결만 쓰므로 NAT/방화벽 환경에서 그대로 동작하고,
catalog.py의 백그라운드 갱신 루프와 같은 패턴으로 관리할 수 있다.

보안: 봇 토큰은 URL에 포함되므로 예외 메시지·요청 URL을 그대로 로그에 남기지
않는다(예외 타입명과 상태 코드만 남긴다). 텔레그램에서 오는 값은 모두 신뢰할 수
없는 입력으로 취급해 타입과 형식을 검사한 뒤에만 사용한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets

import httpx

from server.config import (
    JWT_SECRET_KEY,
    TELEGRAM_API_BASE,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_BOT_USERNAME,
    TELEGRAM_LINK_CODE_TTL,
    TELEGRAM_POLL_TIMEOUT,
    TELEGRAM_SEND_TIMEOUT,
)
from server.database import get_db

logger = logging.getLogger(__name__)

# 헷갈리는 글자(0/O, 1/I)를 뺀 32자 알파벳. 8자리면 32^8 ≈ 1.1e12 조합이라
# 10분 만료 + 일회성과 결합하면 추측이 사실상 불가능하다.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CODE_RE = re.compile(f"^[{CODE_ALPHABET}]{{{CODE_LENGTH}}}$")

OFFSET_STATE_KEY = "telegram_update_offset"
MAX_TEXT_LENGTH = 256  # 봇에게 온 메시지에서 이 길이까지만 살펴본다
POLL_ERROR_BACKOFF = 5
POLL_ERROR_BACKOFF_MAX = 60

_poll_task: asyncio.Task | None = None

HELP_TEXT = (
    "Nikon Value 가격 알림 봇입니다.\n\n"
    "연동하려면 제품 페이지의 '가격 알림' 패널에서 연동 코드를 발급받아 "
    "이 대화에 그대로 보내주세요.\n"
    "연동을 해제하려면 /unlink 를 보내주세요."
)


def is_configured() -> bool:
    """봇 토큰이 설정돼 있는지. 미설정이면 발송·수신 모두 조용히 비활성화된다."""
    return bool(TELEGRAM_BOT_TOKEN)


def bot_username() -> str:
    return TELEGRAM_BOT_USERNAME


def _api_url(method: str) -> str:
    """봇 토큰이 들어간 API URL. 절대 로그·응답에 노출하지 않는다."""
    return f"{TELEGRAM_API_BASE.rstrip('/')}/bot{TELEGRAM_BOT_TOKEN}/{method}"


def _error_label(exc: Exception) -> str:
    """예외를 로그에 남길 때 쓰는 안전한 라벨.

    httpx 예외의 문자열에는 요청 URL(= 봇 토큰)이 포함될 수 있으므로
    타입명과 상태 코드만 남긴다.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTPStatusError(status={exc.response.status_code})"
    return type(exc).__name__


# --- 발송 -------------------------------------------------------------------


async def send_message(chat_id: str, text: str) -> bool:
    """텔레그램 sendMessage. 성공하면 True, 미설정·네트워크 오류·API 오류는 False."""
    if not is_configured():
        logger.debug("TELEGRAM_BOT_TOKEN not set; skipping sendMessage")
        return False
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                _api_url("sendMessage"),
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
                timeout=TELEGRAM_SEND_TIMEOUT,
            )
        if resp.status_code != 200:
            logger.warning("Telegram sendMessage failed with status %d", resp.status_code)
            return False
        payload = resp.json()
        if not (isinstance(payload, dict) and payload.get("ok")):
            # description에는 토큰이 포함되지 않지만, 방어적으로 error_code만 남긴다.
            code = payload.get("error_code") if isinstance(payload, dict) else None
            logger.warning("Telegram sendMessage returned not-ok (error_code=%s)", code)
            return False
        return True
    except Exception as exc:  # 네트워크 오류·JSON 파싱 실패 등 모든 실패는 False
        logger.warning("Telegram sendMessage error: %s", _error_label(exc))
        return False


async def get_chat_id(user_id: int) -> str | None:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT telegram_chat_id FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
    if not row:
        return None
    return row["telegram_chat_id"] or None


# --- 연동 코드 --------------------------------------------------------------


def _hash_code(code: str) -> str:
    """코드 원문 대신 저장할 HMAC 해시(oauth.py의 state 서명과 같은 키를 쓴다)."""
    return hmac.new(JWT_SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


def generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(raw: str) -> str | None:
    """사용자가 보낸 문자열을 코드로 정규화한다. 형식이 맞지 않으면 None."""
    if not isinstance(raw, str):
        return None
    candidate = raw.strip().upper().replace("-", "").replace(" ", "")
    if not CODE_RE.fullmatch(candidate):
        return None
    return candidate


async def issue_link_code(user_id: int) -> tuple[str, int]:
    """사용자에게 새 연동 코드를 발급한다. 기존 대기 코드는 무효화된다."""
    code = generate_code()
    async with get_db() as db:
        # 만료된 코드와 이 사용자의 이전 코드를 정리해 항상 하나만 유효하게 둔다.
        await db.execute("DELETE FROM telegram_link_codes WHERE expires_at <= datetime('now')")
        await db.execute("DELETE FROM telegram_link_codes WHERE user_id = ?", (user_id,))
        await db.execute(
            """INSERT INTO telegram_link_codes (code_hash, user_id, expires_at)
               VALUES (?, ?, datetime('now', ?))""",
            (_hash_code(code), user_id, f"+{TELEGRAM_LINK_CODE_TTL} seconds"),
        )
        await db.commit()
    return code, TELEGRAM_LINK_CODE_TTL


async def consume_link_code(code: str, chat_id: str) -> int | None:
    """코드를 검증하고 chat_id를 사용자에 연결한다. 성공 시 user_id, 실패 시 None.

    코드는 일회성이라 성공·실패와 무관하게 조회된 행을 삭제한다.
    """
    normalized = normalize_code(code)
    if normalized is None:
        return None

    async with get_db() as db:
        cursor = await db.execute(
            """SELECT user_id FROM telegram_link_codes
               WHERE code_hash = ? AND expires_at > datetime('now')""",
            (_hash_code(normalized),),
        )
        row = await cursor.fetchone()
        # 만료 여부와 무관하게 조회 대상 행은 제거한다(재사용 차단 + 정리).
        await db.execute(
            "DELETE FROM telegram_link_codes WHERE code_hash = ?", (_hash_code(normalized),)
        )
        if row is None:
            await db.commit()
            return None

        user_id = row["user_id"]
        # 텔레그램 계정 하나는 사이트 계정 하나에만 연결한다(UNIQUE 인덱스 충돌 방지).
        await db.execute(
            "UPDATE users SET telegram_chat_id = NULL, telegram_linked_at = NULL WHERE telegram_chat_id = ?",
            (chat_id,),
        )
        await db.execute(
            "UPDATE users SET telegram_chat_id = ?, telegram_linked_at = datetime('now') WHERE id = ?",
            (chat_id, user_id),
        )
        await db.commit()
    logger.info("Telegram linked for user %d", user_id)
    return user_id


async def unlink_user(user_id: int) -> bool:
    """사용자의 텔레그램 연동을 해제한다. 연동돼 있었으면 True."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT telegram_chat_id FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        was_linked = bool(row and row["telegram_chat_id"])
        await db.execute(
            "UPDATE users SET telegram_chat_id = NULL, telegram_linked_at = NULL WHERE id = ?",
            (user_id,),
        )
        await db.execute("DELETE FROM telegram_link_codes WHERE user_id = ?", (user_id,))
        await db.commit()
    return was_linked


async def unlink_chat(chat_id: str) -> int | None:
    """봇에서 /unlink 를 받았을 때 해당 chat_id의 연동을 해제한다."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id FROM users WHERE telegram_chat_id = ?", (chat_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        user_id = row["id"]
        await db.execute(
            "UPDATE users SET telegram_chat_id = NULL, telegram_linked_at = NULL WHERE id = ?",
            (user_id,),
        )
        await db.commit()
    return user_id


async def link_status(user_id: int) -> dict:
    """연동 상태 조회. chat_id 원문은 노출하지 않는다."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT telegram_chat_id, telegram_linked_at FROM users WHERE id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
    linked = bool(row and row["telegram_chat_id"])
    return {
        "configured": is_configured(),
        "linked": linked,
        "linked_at": row["telegram_linked_at"] if linked else None,
        "bot_username": TELEGRAM_BOT_USERNAME or None,
    }


# --- 수신(폴링) -------------------------------------------------------------


def _extract_message(update: object) -> tuple[str, str] | None:
    """업데이트에서 (chat_id, text)를 안전하게 뽑아낸다. 신뢰할 수 없는 입력."""
    if not isinstance(update, dict):
        return None
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    if not isinstance(chat_id, int) or isinstance(chat_id, bool):
        return None
    text = message.get("text")
    if not isinstance(text, str):
        return None
    return str(chat_id), text[:MAX_TEXT_LENGTH]


def _parse_command(text: str) -> tuple[str, str]:
    """'/start ABCD1234' → ('/start', 'ABCD1234'), 'ABCD1234' → ('', 'ABCD1234')."""
    parts = text.strip().split()
    if not parts:
        return "", ""
    if parts[0].startswith("/"):
        command = parts[0].split("@", 1)[0].lower()
        return command, parts[1] if len(parts) > 1 else ""
    return "", parts[0]


async def handle_update(update: object) -> None:
    """업데이트 하나를 처리한다. 어떤 입력에도 예외를 밖으로 내보내지 않는다."""
    extracted = _extract_message(update)
    if extracted is None:
        return
    chat_id, text = extracted
    command, argument = _parse_command(text)

    if command == "/unlink":
        user_id = await unlink_chat(chat_id)
        await send_message(
            chat_id,
            "연동을 해제했습니다. 더 이상 가격 알림을 보내지 않습니다."
            if user_id
            else "연동된 계정이 없습니다.",
        )
        return

    if command in ("/help", "/start") and not argument:
        await send_message(chat_id, HELP_TEXT)
        return

    candidate = argument or text
    if normalize_code(candidate) is None:
        await send_message(chat_id, HELP_TEXT)
        return

    if await consume_link_code(candidate, chat_id) is not None:
        await send_message(
            chat_id,
            "연동이 완료되었습니다. 설정하신 목표가에 도달하면 이 대화로 알림을 보내드립니다.",
        )
    else:
        await send_message(
            chat_id,
            "연동 코드가 유효하지 않거나 만료되었습니다(유효 시간 10분). "
            "사이트에서 코드를 다시 발급받아 주세요.",
        )


async def _load_offset() -> int:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT value FROM app_state WHERE key = ?", (OFFSET_STATE_KEY,)
        )
        row = await cursor.fetchone()
    if not row or not row["value"]:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


async def _save_offset(offset: int) -> None:
    async with get_db() as db:
        await db.execute(
            """INSERT INTO app_state (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (OFFSET_STATE_KEY, str(offset)),
        )
        await db.commit()


async def fetch_updates(offset: int) -> list:
    """getUpdates long polling 1회. 실패는 예외로 올려 루프가 백오프하도록 한다."""
    params: dict[str, object] = {
        "timeout": TELEGRAM_POLL_TIMEOUT,
        "allowed_updates": json.dumps(["message"]),
    }
    if offset:
        params["offset"] = offset
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _api_url("getUpdates"),
            params=params,
            timeout=TELEGRAM_POLL_TIMEOUT + 10,
        )
        resp.raise_for_status()
        payload = resp.json()
    if not (isinstance(payload, dict) and payload.get("ok")):
        return []
    result = payload.get("result")
    return result if isinstance(result, list) else []


async def poll_once(offset: int) -> int:
    """업데이트를 한 번 받아 처리하고 다음 offset을 반환한다."""
    updates = await fetch_updates(offset)
    next_offset = offset
    for update in updates:
        if isinstance(update, dict) and isinstance(update.get("update_id"), int):
            next_offset = max(next_offset, update["update_id"] + 1)
        try:
            await handle_update(update)
        except Exception as exc:
            # 업데이트 하나가 실패해도 나머지 처리와 offset 전진을 막지 않는다.
            logger.warning("Telegram update handling failed: %s", _error_label(exc))
    if next_offset != offset:
        await _save_offset(next_offset)
    return next_offset


async def _poll_loop() -> None:
    offset = await _load_offset()
    backoff = POLL_ERROR_BACKOFF
    logger.info("Telegram polling started (offset=%d)", offset)
    while True:
        try:
            offset = await poll_once(offset)
            backoff = POLL_ERROR_BACKOFF
        except Exception as exc:
            logger.warning(
                "Telegram getUpdates failed: %s (retry in %ds)", _error_label(exc), backoff
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, POLL_ERROR_BACKOFF_MAX)


def start_polling() -> None:
    """토큰이 설정된 경우에만 백그라운드 폴링 루프를 띄운다."""
    global _poll_task
    if not is_configured():
        logger.info("TELEGRAM_BOT_TOKEN not set; Telegram polling disabled")
        return
    if _poll_task is not None:
        return
    _poll_task = asyncio.create_task(_poll_loop())


def stop_polling() -> None:
    global _poll_task
    if _poll_task:
        _poll_task.cancel()
        _poll_task = None
