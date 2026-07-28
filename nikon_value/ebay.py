"""eBay Browse API 클라이언트와 적응형 가격 탐색."""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, replace

import requests

from nikon_value.filters import filter_items_with_rules
from nikon_value.metrics import RunMetrics, resolve_metrics

log = logging.getLogger(__name__)

EBAY_AUTH_URL_PROD = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_AUTH_URL_SANDBOX = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_URL_PROD = "https://api.ebay.com/buy/browse/v1/item_summary/search"
EBAY_BROWSE_URL_SANDBOX = "https://api.sandbox.ebay.com/buy/browse/v1/item_summary/search"

ADAPTIVE_MAX_PRICE_STEPS = (
    (1.5, 200),
    (2.0, 800),
)
ADAPTIVE_MAX_PRICE_TRIGGER_RATIO = 0.98
RATE_LIMIT_MAX_RETRIES = 6
RATE_LIMIT_BASE_WAIT_SECONDS = 5
RATE_LIMIT_MAX_WAIT_SECONDS = 160


def get_ebay_urls(sandbox: bool) -> tuple[str, str]:
    """환경에 맞는 eBay API URL을 반환합니다."""
    if sandbox:
        return EBAY_AUTH_URL_SANDBOX, EBAY_BROWSE_URL_SANDBOX
    return EBAY_AUTH_URL_PROD, EBAY_BROWSE_URL_PROD


@dataclass(frozen=True)
class SearchConstraints:
    """Browse API 검색을 좁히는 제약 묶음.

    기본값이 곧 정규 수집의 조건이다. 진단 프로브는 :func:`dataclasses.replace`로
    제약을 하나씩만 풀어 "어느 제약이 매물을 전부 걷어냈는지"를 가려낸다.
    """

    condition_ids: tuple[str, ...] = ("3000",)  # USED
    price_window: bool = True
    delivery_country: str | None = "KR"
    buying_options: tuple[str, ...] = ("FIXED_PRICE",)
    use_category: bool = True


DEFAULT_SEARCH_CONSTRAINTS = SearchConstraints()
# 모든 제약을 푼 상태. "q가 eBay 전체에서 뭐라도 잡는가"를 확인하는 데 쓴다.
UNCONSTRAINED_SEARCH = SearchConstraints(
    condition_ids=(),
    price_window=False,
    delivery_country=None,
    buying_options=(),
    use_category=False,
)


def relax(constraints: SearchConstraints, **changes) -> SearchConstraints:
    """제약 하나를 바꾼 사본을 만듭니다(프로브 정의용 얇은 헬퍼)."""
    return replace(constraints, **changes)


def build_search_filter(
    min_price: float,
    max_price: float,
    constraints: SearchConstraints = DEFAULT_SEARCH_CONSTRAINTS,
) -> str:
    """Browse API `filter` 파라미터 문자열을 만듭니다."""
    parts = []
    if constraints.condition_ids:
        parts.append(f"conditionIds:{{{'|'.join(constraints.condition_ids)}}}")
    if constraints.price_window:
        parts.append(f"price:[{min_price}..{max_price}]")
        parts.append("priceCurrency:USD")
    if constraints.delivery_country:
        parts.append(f"deliveryCountry:{constraints.delivery_country}")
    if constraints.buying_options:
        parts.append(f"buyingOptions:{{{'|'.join(constraints.buying_options)}}}")
    return ",".join(parts)


def build_search_params(
    query: str,
    category_id: str | None,
    min_price: float,
    max_price: float,
    constraints: SearchConstraints = DEFAULT_SEARCH_CONSTRAINTS,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """Browse API 검색 쿼리 파라미터를 만듭니다.

    정규 수집과 진단 프로브가 같은 함수를 쓰기 때문에, 프로브의 baseline은
    정규 수집과 문자 그대로 동일한 요청이 된다(비교의 기준선이 흔들리지 않는다).
    """
    params = {
        "q": query,
        "sort": "price",
        "limit": limit,
        "offset": offset,
        "fieldgroups": "MATCHING_ITEMS",
    }
    search_filter = build_search_filter(min_price, max_price, constraints)
    if search_filter:
        params["filter"] = search_filter
    if category_id and constraints.use_category:
        params["category_ids"] = category_id
    return params


def build_search_headers(token: str) -> dict:
    """Browse API 요청 헤더를 만듭니다."""
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    # eBay Partner Network 캠페인 ID가 설정된 경우에만 어필리에이트 컨텍스트를 보낸다.
    epn_campaign_id = os.environ.get("EBAY_EPN_CAMPAIGN_ID")
    if epn_campaign_id:
        headers["X-EBAY-C-ENDUSERCTX"] = f"affiliateCampaignId={epn_campaign_id}"
    return headers


def get_access_token(client_id: str, client_secret: str, auth_url: str) -> str:
    """OAuth 2.0 client credentials grant로 액세스 토큰을 발급받습니다."""
    resp = requests.post(
        auth_url,
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    log.info("eBay access token acquired")
    return token


def search_items(token: str, browse_url: str, query: str, category_id: str | None,
                 min_price: float, max_price: float,
                 metrics: RunMetrics | None = None) -> list[dict]:
    """Browse API로 중고 매물을 검색합니다. 페이지네이션 처리."""
    run_metrics = resolve_metrics(metrics)
    headers = build_search_headers(token)

    all_items = []
    offset = 0
    limit = 200
    rate_limit_retries = 0

    while True:
        params = build_search_params(
            query, category_id, min_price, max_price,
            limit=limit, offset=offset,
        )

        resp = requests.get(
            browse_url,
            headers=headers,
            params=params,
            timeout=30,
        )
        run_metrics.record_http_request()

        if resp.status_code == 429:
            run_metrics.record_rate_limited()
            rate_limit_retries += 1
            if rate_limit_retries > RATE_LIMIT_MAX_RETRIES:
                log.error("Rate limited %d times, giving up on this search", rate_limit_retries - 1)
                resp.raise_for_status()
            wait = min(
                RATE_LIMIT_BASE_WAIT_SECONDS * 2 ** (rate_limit_retries - 1),
                RATE_LIMIT_MAX_WAIT_SECONDS,
            )
            log.warning(
                "Rate limited, waiting %d seconds (retry %d/%d)...",
                wait, rate_limit_retries, RATE_LIMIT_MAX_RETRIES,
            )
            time.sleep(wait)
            continue

        resp.raise_for_status()
        data = resp.json()

        items = data.get("itemSummaries", [])
        if not items:
            break

        all_items.extend(items)
        total = data.get("total", 0)

        if offset + limit >= total or offset + limit >= 10000:
            break

        offset += limit
        time.sleep(0.5)  # 예의 바른 크롤링

    return all_items


def extract_price(item: dict) -> float | None:
    """아이템에서 배송비 포함 가격을 추출합니다."""
    price_info = item.get("price", {})
    price_val = price_info.get("value")
    if price_val is None:
        return None

    total = float(price_val)

    # 배송비 추가
    shipping_options = item.get("shippingOptions", [])
    if shipping_options:
        shipping_cost = shipping_options[0].get("shippingCost", {})
        shipping_val = shipping_cost.get("value")
        if shipping_val:
            total += float(shipping_val)

    return round(total, 2)


def collect_prices(items: list[dict]) -> list[float]:
    """Extract comparable listing prices from eBay items."""
    prices = []
    for item in items:
        price = extract_price(item)
        if price is not None:
            prices.append(price)
    return prices


def round_price_bound(value: float) -> int:
    """Round adaptive search bounds to predictable price increments."""
    if value >= 20000:
        step = 1000
    elif value >= 10000:
        step = 500
    elif value >= 2000:
        step = 100
    elif value >= 500:
        step = 50
    else:
        step = 25
    return int(math.ceil(value / step) * step)


def should_expand_max_price(
    prices: list[float],
    current_max_price: float,
    expand_when_empty: bool = True,
) -> bool:
    """Retry with a higher max when the current price window looks clipped.

    `expand_when_empty=False`는 "상한이 낮아서 못 찾은 게 아니라 eBay에 매물이
    없다"고 판단된 제품(연속 0건 누적)에 쓴다. 빈 결과로는 확장하지 않는다.
    """
    if not prices:
        return expand_when_empty
    return max(prices) >= current_max_price * ADAPTIVE_MAX_PRICE_TRIGGER_RATIO


def search_items_for_product(
    token: str,
    browse_url: str,
    product: dict,
    expand_when_empty: bool = True,
    metrics: RunMetrics | None = None,
) -> tuple[list[dict], float]:
    """Search a product and retry with higher max prices when needed."""
    run_metrics = resolve_metrics(metrics)
    category_id = product.get("search_category_id", product["category_id"])
    min_price = float(product["min_price"])
    base_max_price = float(product["max_price"])

    run_metrics.record_ebay_search()
    best_items = filter_items_with_rules(
        search_items(
            token,
            browse_url,
            product["query"],
            category_id,
            min_price,
            base_max_price,
        ),
        product,
    )
    best_prices = collect_prices(best_items)
    best_max_price = base_max_price
    trial_max_price = base_max_price

    for multiplier, min_delta in ADAPTIVE_MAX_PRICE_STEPS:
        if not should_expand_max_price(best_prices, trial_max_price, expand_when_empty):
            break

        candidate_max_price = round_price_bound(
            max(base_max_price * multiplier, trial_max_price + min_delta)
        )
        if candidate_max_price <= trial_max_price:
            continue

        run_metrics.record_ebay_search()
        run_metrics.record_max_price_expansion()
        candidate_items = filter_items_with_rules(
            search_items(
                token,
                browse_url,
                product["query"],
                category_id,
                min_price,
                candidate_max_price,
            ),
            product,
        )
        candidate_prices = collect_prices(candidate_items)

        has_more_items = len(candidate_items) > len(best_items)
        has_higher_prices = (
            bool(candidate_prices)
            and (not best_prices or max(candidate_prices) > max(best_prices))
        )

        if has_more_items or has_higher_prices:
            previous_count = len(best_items)
            previous_max = max(best_prices) if best_prices else None
            best_items = candidate_items
            best_prices = candidate_prices
            best_max_price = candidate_max_price
            log.info(
                "  Expanded max price: $%s -> $%s (%d -> %d items, max observed $%s -> $%s)",
                round(base_max_price, 2),
                round(candidate_max_price, 2),
                previous_count,
                len(candidate_items),
                previous_max,
                max(candidate_prices) if candidate_prices else None,
            )

        trial_max_price = candidate_max_price

    return best_items, best_max_price
