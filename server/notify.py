"""이메일 발송 유틸 (stdlib smtplib, 워커 스레드에서 실행).

SMTP_HOST가 설정되지 않으면 발송하지 않고 False를 반환한다 — 알림 체커가
'발송 성공 시에만 상태 갱신' 규칙으로 재시도를 자연스럽게 처리한다.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from server.config import SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USERNAME

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(SMTP_HOST)


def _send_sync(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = SMTP_FROM or SMTP_USERNAME
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            if SMTP_USERNAME:
                smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(msg)
        return

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.starttls()
        if SMTP_USERNAME:
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.send_message(msg)


async def send_email(to: str, subject: str, body: str) -> bool:
    """발송 성공 여부를 반환한다. 미설정/실패 시 False."""
    if not is_configured():
        logger.info("SMTP not configured, skipping email to %s (%s)", to, subject)
        return False
    try:
        await asyncio.to_thread(_send_sync, to, subject, body)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to)
        return False
