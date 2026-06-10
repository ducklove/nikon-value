"""중앙값 대비 큰 폭으로 저렴한 '딜' 매물 추출.

필터를 통과한 매물 중 중앙값의 일정 비율 이하로 싼 것을 골라 카탈로그에
싣는다. 지나치게 싼 매물(중앙값의 40% 미만)은 필터가 놓친 부품·오변형
매물일 가능성이 높아 제외한다.
"""

from __future__ import annotations

from nikon_value.ebay import extract_price

DEAL_MAX_RATIO = 0.8
DEAL_MIN_RATIO = 0.4
DEAL_MAX_PER_PRODUCT = 3


def extract_deal_listings(
    items: list[dict], median: float | None, max_deals: int = DEAL_MAX_PER_PRODUCT
) -> list[dict]:
    """배송비 포함가가 중앙값의 40~80% 구간인 매물을 싼 순으로 반환합니다."""
    if not median or median <= 0:
        return []

    deals = []
    for item in items:
        price = extract_price(item)
        if price is None:
            continue
        url = item.get("itemWebUrl", "")
        if not url:
            continue
        ratio = price / median
        if ratio > DEAL_MAX_RATIO or ratio < DEAL_MIN_RATIO:
            continue
        thumbnails = item.get("thumbnailImages") or [{}]
        deals.append({
            "title": item.get("title", ""),
            "price": price,
            "discount_pct": round((1 - ratio) * 100, 1),
            "image": thumbnails[0].get("imageUrl", ""),
            "url": url,
        })

    deals.sort(key=lambda deal: deal["price"])
    return deals[:max_deals]
