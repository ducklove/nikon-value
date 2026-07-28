"""가격 통계(IQR 아웃라이어 제거)와 샘플 매물 추출."""

from __future__ import annotations

import statistics

from nikon_value.ebay import extract_price


def compute_stats(prices: list[float]) -> dict:
    """IQR 아웃라이어 제거 후 통계를 계산합니다."""
    if not prices:
        return {
            "median": None,
            "mean": None,
            "min": None,
            "max": None,
            "q1": None,
            "q3": None,
            "count": 0,
            "count_filtered": 0,
        }

    prices_sorted = sorted(prices)
    n = len(prices_sorted)

    if n < 4:
        # 데이터가 너무 적으면 아웃라이어 제거 없이 계산
        return {
            "median": round(statistics.median(prices_sorted), 2),
            "mean": round(statistics.mean(prices_sorted), 2),
            "min": round(prices_sorted[0], 2),
            "max": round(prices_sorted[-1], 2),
            "q1": round(prices_sorted[0], 2),
            "q3": round(prices_sorted[-1], 2),
            "count": n,
            "count_filtered": n,
        }

    q1 = statistics.median(prices_sorted[: n // 2])
    q3 = statistics.median(prices_sorted[(n + 1) // 2 :])
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr

    filtered = [p for p in prices_sorted if lower <= p <= upper]
    if not filtered:
        filtered = prices_sorted

    return {
        "median": round(statistics.median(filtered), 2),
        "mean": round(statistics.mean(filtered), 2),
        "min": round(min(filtered), 2),
        "max": round(max(filtered), 2),
        "q1": round(q1, 2),
        "q3": round(q3, 2),
        "count": n,
        "count_filtered": len(filtered),
    }


def extract_sample_listings(items: list[dict], max_samples: int = 5) -> list[dict]:
    """차트 아래 표시할 샘플 매물을 추출합니다."""
    priced_items = []
    for item in items:
        price = extract_price(item)
        if price is None:
            continue
        priced_items.append((item, price))

    if len(priced_items) > max_samples:
        center = len(priced_items) // 2
        start = max(0, center - (max_samples // 2))
        priced_items = priced_items[start : start + max_samples]

    samples = []
    for item, price in priced_items:
        # thumbnailImages는 키가 없을 수도, 빈 리스트일 수도 있다.
        thumbnails = item.get("thumbnailImages") or [{}]
        samples.append({
            "title": item.get("title", ""),
            "price": price,
            "currency": item.get("price", {}).get("currency", "USD"),
            "condition": item.get("condition", ""),
            "image": thumbnails[0].get("imageUrl", ""),
            # itemAffiliateWebUrl은 EBAY_EPN_CAMPAIGN_ID가 설정돼 X-EBAY-C-ENDUSERCTX
            # 헤더를 보낸 경우에만 내려온다. eBay 파트너 네트워크 커미션은 이 URL로
            # 유입된 트래픽에만 발생하므로 있으면 우선 쓰고, 없으면 기존 동작을 유지한다.
            "url": item.get("itemAffiliateWebUrl") or item.get("itemWebUrl", ""),
        })
    return samples
