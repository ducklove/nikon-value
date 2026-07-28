"""가격 하락 알림 체커.

카탈로그가 갱신될 때마다 등록된 알림의 목표가와 현재 중앙값을 비교한다.
중앙값이 목표가 이하로 내려가면 알림을 한 번 보내고 triggered=1로 표시,
중앙값이 목표가 위로 회복되면 triggered=0으로 재무장(re-arm)해 다음 하락 때
다시 알린다. 발송에 성공했을 때만 상태를 갱신하므로 사용자가 아직 알림 채널
(텔레그램)을 연동하지 않았거나 채널이 일시 장애인 경우 알림이 소실되지 않고
다음 주기에 자연스럽게 재시도된다.
"""

from __future__ import annotations

import logging

from server import catalog
from server.config import FRONTEND_URL
from server.database import get_db
from server.notify import send_price_alert

logger = logging.getLogger(__name__)


def _build_alert_message(product_id: str, name: str, median: float, target_price: float) -> tuple[str, str]:
    subject = f"[Nikon Value] {name} 시세가 목표가에 도달했습니다"
    product_url = f"{FRONTEND_URL.rstrip('/')}/products/{product_id}.html"
    body = (
        f"{name}의 eBay 매물 중앙값이 설정하신 목표가에 도달했습니다.\n"
        f"\n"
        f"현재 중앙값: ${median:,.2f}\n"
        f"목표가: ${target_price:,.2f}\n"
        f"\n"
        f"시세 추이 보기: {product_url}\n"
        f"\n"
        f"가격은 현재 매물 기준이며 실제 거래가와 다를 수 있습니다.\n"
        f"알림은 가격이 목표가 위로 회복된 뒤 다시 하락하면 한 번 더 발송됩니다."
    )
    return subject, body


async def check_price_alerts() -> int:
    """알림을 점검하고 발송한 알림 수를 반환한다."""
    if not catalog.is_loaded() or catalog.product_count() == 0:
        return 0

    sent = 0
    pending = 0
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, user_id, product_id, target_price, triggered FROM price_alerts"
        )
        rows = await cursor.fetchall()

        for row in rows:
            median = catalog.get_median(row["product_id"])
            if median is None:
                continue

            if median <= row["target_price"] and not row["triggered"]:
                name = catalog.get_product_name(row["product_id"])
                subject, body = _build_alert_message(
                    row["product_id"], name, median, row["target_price"]
                )
                if await send_price_alert(row["user_id"], subject, body):
                    await db.execute(
                        "UPDATE price_alerts SET triggered = 1, updated_at = datetime('now') WHERE id = ?",
                        (row["id"],),
                    )
                    sent += 1
                else:
                    pending += 1
            elif median > row["target_price"] and row["triggered"]:
                await db.execute(
                    "UPDATE price_alerts SET triggered = 0, updated_at = datetime('now') WHERE id = ?",
                    (row["id"],),
                )

        await db.commit()

    if sent:
        logger.info("Price alerts sent: %d", sent)
    if pending:
        logger.info("Price alerts pending delivery (channel not configured or send failed): %d", pending)
    return sent
