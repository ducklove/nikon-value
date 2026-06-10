#!/usr/bin/env python3
"""eBay Browse API를 사용하여 니콘 중고 장비 시세를 수집합니다."""

import argparse
import json
import logging
import math
import os
import re
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import requests
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "products.yaml"
DATA_DIR = PROJECT_ROOT / "data"

EBAY_AUTH_URL_PROD = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_AUTH_URL_SANDBOX = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
EBAY_BROWSE_URL_PROD = "https://api.ebay.com/buy/browse/v1/item_summary/search"
EBAY_BROWSE_URL_SANDBOX = "https://api.sandbox.ebay.com/buy/browse/v1/item_summary/search"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_DEFAULT_MODEL = "openai/gpt-5.4-nano"


def _openrouter_model() -> str:
    """환경변수 OPENROUTER_MODEL로 모델을 지정할 수 있습니다."""
    return os.environ.get("OPENROUTER_MODEL", OPENROUTER_DEFAULT_MODEL)
ECB_EXCHANGE_RATES_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

MAX_DAILY_SNAPSHOTS = 400
MAX_PRODUCT_HISTORY = 365
ADAPTIVE_MAX_PRICE_STEPS = (
    (1.5, 200),
    (2.0, 800),
)
ADAPTIVE_MAX_PRICE_TRIGGER_RATIO = 0.98
RATE_LIMIT_MAX_RETRIES = 6
RATE_LIMIT_BASE_WAIT_SECONDS = 5
RATE_LIMIT_MAX_WAIT_SECONDS = 160

COMMON_EXCLUDE_PATTERNS = [
    " for parts",
    " parts only",
    " not working",
    " broken",
    " repair",
    " manual",
    " instruction",
    " empty box",
    " box only",
    " packaging only",
    " body cap",
    " rear cap",
    " front cap",
    " battery",
    " charger",
    " strap",
    " adapter",
    " filter",
    " grip",
    " eyepiece",
    " focusing screen",
    " viewfinder",
    " motor drive",
    " screen protector",
    " camera case",
    " lens case",
    " bag only",
    " case only",
    " cap only",
    " bundle",
    " issues",
    " issue",
    " untested",
    " as is",
    " as-is",
    " junk",
]
ACCESSORY_ALLOWED_PATTERNS = {
    " focusing screen",
    " viewfinder",
    " motor drive",
}
LENS_HOOD_RE = re.compile(r"\b(?:hood|shade|hb-\d+|hn-\d+|hr-\d+|hs-\d+|hk-\d+|he-\d+|hf-\d+)\b")
CAMERA_BODY_EXCLUDE_PATTERNS = [
    " lens ",
    " nikkor ",
    " sigma ",
    " tamron ",
    " tokina ",
    " teleconverter ",
    " tc-",
    " lens kit",
    " kit lens",
]
AI_S_TOKEN_RE = re.compile(r"\b(?:ai-s|ai s|ais)\b")
AI_TOKEN_RE = re.compile(r"\bai\b")
NON_AI_TOKEN_RE = re.compile(r"\b(?:non[- ]ai|new nikkor|nikkor-[a-z.]+ auto|nikkor [a-z.]+ auto|auto)\b")
AF_TOKEN_RE = re.compile(r"\b(?:af(?:-s|-p|-d)?|af nikkor|autofocus|auto focus)\b")
SERIES_E_TOKEN_RE = re.compile(r"\b(?:series e|e series)\b")


def parse_args() -> argparse.Namespace:
    """CLI 인자를 파싱합니다."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--sandbox", action="store_true")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="특정 제품 ID만 갱신합니다. 쉼표로 여러 개 지정 가능",
    )
    return parser.parse_args()


def parse_only_ids(values: list[str]) -> set[str]:
    """--only 인자를 제품 ID 집합으로 변환합니다."""
    result = set()
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if item:
                result.add(item)
    return result


def load_text_secret(filename: str, env_name: str) -> str | None:
    """KEY=VALUE 또는 raw text 형식의 시크릿 파일을 읽습니다."""
    key_file = PROJECT_ROOT / filename
    if key_file.exists():
        with open(key_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    _, value = line.split("=", 1)
                    return value.strip()
                return line
    return os.environ.get(env_name)


def load_openrouter_key() -> str | None:
    """openrouter.key 파일 또는 OPENROUTER_API_KEY 환경변수에서 API 키를 로드합니다."""
    return load_text_secret("openrouter.key", "OPENROUTER_API_KEY")


def extract_openrouter_message_text(data: dict) -> str:
    """OpenRouter chat completion payload에서 텍스트 응답을 추출합니다."""
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if not text and isinstance(item.get("content"), str):
                    text = item["content"]
                if text:
                    parts.append(text)
        joined = "".join(parts).strip()
        if joined:
            return joined

    raise ValueError("OpenRouter response did not contain text content")


def strip_json_code_fence(text: str) -> str:
    """Remove optional ```json fenced wrappers around structured output."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def extract_openrouter_indices(data: dict) -> list[int]:
    """Parse structured JSON indices from an OpenRouter response."""
    payload = json.loads(strip_json_code_fence(extract_openrouter_message_text(data)))
    if isinstance(payload, list):
        indices = payload
    elif isinstance(payload, dict):
        indices = payload.get("indices")
    else:
        raise ValueError("OpenRouter response JSON must be a list or object")

    if not isinstance(indices, list):
        raise ValueError("OpenRouter response is missing an indices list")

    return indices


def filter_items_with_llm(
    items: list[dict], product: dict, openrouter_key: str
) -> list[dict]:
    """OpenRouter API를 사용하여 리스팅 타이틀이 실제 해당 제품인지 검증합니다."""
    if not items:
        return items

    titles = [item.get("title", "") for item in items]
    listings_text = "\n".join(f"{i}: \"{t}\"" for i, t in enumerate(titles))

    is_accessory = product.get("product_type") == "accessory"
    exclude_lines = [
        "- Different camera/lens/accessory models",
        "- Accessories, grips, batteries, straps, caps, filters, adapters, cases",
        "- Kits or bundles (unless the product itself is a kit)",
        "- Parts, repairs, or \"for parts\" listings",
        "- Manuals, boxes, or packaging only",
    ]
    if is_accessory:
        exclude_lines.insert(
            1,
            "- Camera bodies, lenses, and unrelated accessories must be excluded",
        )
    else:
        exclude_lines.insert(
            1,
            "- IMPORTANT: Lens hoods MUST be excluded. Any title containing \"hood\", \"shade\", or hood model numbers (HB-*, HN-*, HR-*, HS-*, HK-*, HE-*, HF-*) is NOT the lens itself — exclude it even if the lens name appears in the title",
        )
        exclude_lines.insert(
            3,
            "- Viewfinders, focusing screens, eyepieces, motor drives, and other camera body parts sold separately",
        )

    prompt = (
        "You are a camera/lens equipment expert. "
        "I need to find listings that are selling exactly this product:\n"
        f"Product: {product['name_en']}\n"
        f"Search query used: {product['query']}\n\n"
        "Below are eBay listing titles. Return ONLY a JSON object in this form:\n"
        "{\"indices\": [0, 2, 4]}\n"
        "Use 0-based indices for listings that ARE actually selling this specific product.\n\n"
        "Exclude:\n"
        + "\n".join(exclude_lines)
        + "\n"
        + ("- Lens-only listings when the product is a camera body\n" if not is_accessory else "")
        + "\n"
        f"Listings:\n{listings_text}"
    )

    try:
        resp = requests.post(
            OPENROUTER_API_URL,
            headers={
                "Authorization": f"Bearer {openrouter_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://ducklove.github.io/nikon-value",
                "X-Title": "Nikon Value",
            },
            json={
                "model": _openrouter_model(),
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a camera gear classifier. "
                            "Return only a JSON object with an indices array."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 512,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        indices = extract_openrouter_indices(data)

        if not isinstance(indices, list):
            log.warning("  LLM returned non-list, skipping filter")
            return items

        valid_indices = [i for i in indices if isinstance(i, int) and 0 <= i < len(items)]
        filtered = [items[i] for i in valid_indices]

        if not filtered:
            if len(items) <= 5:
                log.info("  LLM filtered all %d items — accepting (small set)", len(items))
                return items
            log.warning("  LLM filtered all %d items — suspicious, keeping heuristic-filtered set", len(items))
            return items

        return filtered

    except Exception as e:
        log.warning("  LLM filter failed (%s), keeping heuristic-filtered set", e)
        return items


def normalize_title(title: str) -> str:
    """간단한 키워드 매칭을 위해 타이틀을 정규화합니다."""
    text = re.sub(r"[^a-z0-9/+.-]+", " ", title.lower())
    return f" {text} "


def matches_product_exclude_patterns(normalized_title: str, product: dict) -> bool:
    """Product-specific title fragments that should always exclude a listing."""
    patterns = product.get("exclude_title_patterns") or []
    for pattern in patterns:
        if pattern and normalize_title(str(pattern)) in normalized_title:
            return True
    return False


def get_title_variant_group(product: dict) -> str | None:
    """제품 ID를 바탕으로 수동 렌즈 세대 그룹을 판별합니다."""
    pid = product.get("id", "")
    if pid.startswith("ai-s-"):
        return "ai-s"
    if pid.startswith("series-e-"):
        return "series-e"
    if pid.startswith("nikkor-auto-") or pid.startswith("micro-nikkor-auto-") or pid.startswith("noct-nikkor-"):
        return "non-ai"
    if pid.startswith("nikkor-") and pid.endswith("-ai"):
        return "ai"
    return None


def is_variant_conflict(title: str, product: dict) -> bool:
    """수동 렌즈 세대가 다른 매물인지 판별합니다."""
    variant_group = get_title_variant_group(product)
    if not variant_group:
        return False

    normalized = normalize_title(title)
    has_ai_s = bool(AI_S_TOKEN_RE.search(normalized))
    has_ai = bool(AI_TOKEN_RE.search(normalized))
    has_non_ai = bool(NON_AI_TOKEN_RE.search(normalized))
    has_af = bool(AF_TOKEN_RE.search(normalized))
    has_series_e = bool(SERIES_E_TOKEN_RE.search(normalized))

    if variant_group == "ai-s":
        return has_non_ai or has_af or has_series_e or not has_ai_s

    if variant_group == "ai":
        return has_non_ai or has_af or has_series_e or has_ai_s or not has_ai

    if variant_group == "non-ai":
        return has_af or has_ai_s or has_series_e or (has_ai and not has_non_ai)

    if variant_group == "series-e":
        return has_af or not has_series_e

    return False


def is_camera_body_product(product: dict) -> bool:
    """카메라 바디 분류인지 판별합니다."""
    return product.get("category_id") in {"31388", "3323"}


def is_obvious_non_match(title: str, product: dict) -> bool:
    """명백한 비매칭/액세서리 매물을 규칙 기반으로 제거합니다."""
    normalized = normalize_title(title)
    exclude_patterns = COMMON_EXCLUDE_PATTERNS
    if product.get("product_type") == "accessory":
        exclude_patterns = [
            pattern for pattern in COMMON_EXCLUDE_PATTERNS
            if pattern not in ACCESSORY_ALLOWED_PATTERNS
        ]

    if any(pattern in normalized for pattern in exclude_patterns):
        return True
    if matches_product_exclude_patterns(normalized, product):
        return True
    if LENS_HOOD_RE.search(normalized):
        return True
    if is_variant_conflict(title, product):
        return True

    if is_camera_body_product(product):
        if any(pattern in normalized for pattern in CAMERA_BODY_EXCLUDE_PATTERNS):
            return True

    return False


def filter_items_with_rules(items: list[dict], product: dict) -> list[dict]:
    """LLM 이전에 명백한 비매칭을 제거합니다."""
    if not items:
        return items

    filtered = [
        item
        for item in items
        if not is_obvious_non_match(item.get("title", ""), product)
    ]

    if not filtered:
        log.warning("  Rule filter removed all items, keeping original set")
        return items

    if len(filtered) != len(items):
        log.info("  Rule filter: %d → %d items", len(items), len(filtered))

    return filtered


def get_ebay_urls(sandbox: bool) -> tuple[str, str]:
    """환경에 맞는 eBay API URL을 반환합니다."""
    if sandbox:
        return EBAY_AUTH_URL_SANDBOX, EBAY_BROWSE_URL_SANDBOX
    return EBAY_AUTH_URL_PROD, EBAY_BROWSE_URL_PROD


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
                 min_price: float, max_price: float) -> list[dict]:
    """Browse API로 중고 매물을 검색합니다. 페이지네이션 처리."""
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    # eBay Partner Network 캠페인 ID가 설정된 경우에만 어필리에이트 컨텍스트를 보낸다.
    epn_campaign_id = os.environ.get("EBAY_EPN_CAMPAIGN_ID")
    if epn_campaign_id:
        headers["X-EBAY-C-ENDUSERCTX"] = f"affiliateCampaignId={epn_campaign_id}"

    all_items = []
    offset = 0
    limit = 200
    rate_limit_retries = 0

    while True:
        params = {
            "q": query,
            "filter": ",".join([
                "conditionIds:{3000}",  # USED
                f"price:[{min_price}..{max_price}]",
                "priceCurrency:USD",
                "deliveryCountry:KR",
                "buyingOptions:{FIXED_PRICE}",
            ]),
            "sort": "price",
            "limit": limit,
            "offset": offset,
            "fieldgroups": "MATCHING_ITEMS",
        }

        if category_id:
            params["category_ids"] = category_id

        resp = requests.get(
            browse_url,
            headers=headers,
            params=params,
            timeout=30,
        )

        if resp.status_code == 429:
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


def should_expand_max_price(prices: list[float], current_max_price: float) -> bool:
    """Retry with a higher max when the current price window looks clipped."""
    if not prices:
        return True
    return max(prices) >= current_max_price * ADAPTIVE_MAX_PRICE_TRIGGER_RATIO


def search_items_for_product(
    token: str,
    browse_url: str,
    product: dict,
) -> tuple[list[dict], float]:
    """Search a product and retry with higher max prices when needed."""
    category_id = product.get("search_category_id", product["category_id"])
    min_price = float(product["min_price"])
    base_max_price = float(product["max_price"])

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
        if not should_expand_max_price(best_prices, trial_max_price):
            break

        candidate_max_price = round_price_bound(
            max(base_max_price * multiplier, trial_max_price + min_delta)
        )
        if candidate_max_price <= trial_max_price:
            continue

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
            "url": item.get("itemWebUrl", ""),
        })
    return samples


def load_catalog() -> dict:
    """products.yaml를 로드합니다."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_existing_catalog_output() -> dict | None:
    """기존 catalog.json을 로드합니다."""
    catalog_path = DATA_DIR / "catalog.json"
    if not catalog_path.exists():
        return None
    with open(catalog_path, encoding="utf-8") as f:
        return json.load(f)


def load_daily_snapshot_for_date(date_str: str) -> dict:
    """특정 날짜의 기존 일별 스냅샷을 로드합니다."""
    filepath = DATA_DIR / "daily" / f"{date_str}.json"
    if not filepath.exists():
        return {"date": date_str, "products": {}}
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    data["date"] = date_str
    data.setdefault("products", {})
    return data


def build_base_product_entry(product: dict) -> dict:
    """설정 기반 기본 제품 메타데이터를 만듭니다."""
    entry = {
        "id": product["id"],
        "name_ko": product["name_ko"],
        "name_en": product["name_en"],
    }
    if "subcategory" in product:
        entry["subcategory"] = product["subcategory"]
    if "release_year" in product:
        entry["release_year"] = product["release_year"]
    if "focal_length_min" in product:
        entry["focal_length_min"] = product["focal_length_min"]
    if "is_rare" in product:
        entry["is_rare"] = product["is_rare"]
    if "rarity_tier" in product:
        entry["rarity_tier"] = product["rarity_tier"]
    if "rarity_sort" in product:
        entry["rarity_sort"] = product["rarity_sort"]
    if "rarity_price_hint" in product:
        entry["rarity_price_hint"] = product["rarity_price_hint"]
    if "rarity_note" in product:
        entry["rarity_note"] = product["rarity_note"]
    return entry


def update_product_history(product_id: str, date_str: str, stats: dict):
    """제품별 시계열 JSON을 업데이트합니다."""
    filepath = DATA_DIR / "products" / f"{product_id}.json"

    history = []
    if filepath.exists():
        with open(filepath, encoding="utf-8") as f:
            history = json.load(f)

    # 같은 날짜 데이터가 있으면 교체
    history = [h for h in history if h["date"] != date_str]
    history.append({
        "date": date_str,
        **stats,
    })

    # 날짜순 정렬 후 롤링
    history.sort(key=lambda x: x["date"])
    history = history[-MAX_PRODUCT_HISTORY:]

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)


def cleanup_daily_snapshots():
    """오래된 일별 스냅샷을 삭제합니다."""
    daily_dir = DATA_DIR / "daily"
    if not daily_dir.exists():
        return

    files = sorted(daily_dir.glob("*.json"))
    if len(files) > MAX_DAILY_SNAPSHOTS:
        for f in files[: len(files) - MAX_DAILY_SNAPSHOTS]:
            f.unlink()
            log.info("Deleted old snapshot: %s", f.name)


def load_env_file(path: Path):
    """KEY=VALUE 형식의 환경변수 파일을 로드합니다."""
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    log.info("Loaded credentials from %s", path)


def _recover_exchange_rate_from_daily() -> dict | None:
    """가장 최근 일별 스냅샷에서 환율 정보를 복구합니다."""
    daily_dir = DATA_DIR / "daily"
    if not daily_dir.exists():
        return None
    for f in sorted(daily_dir.glob("*.json"), reverse=True)[:5]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            rate = data.get("exchange_rate")
            if rate and rate.get("rate"):
                return rate
        except Exception:
            continue
    return None


def fetch_usd_krw_exchange_rate() -> dict[str, object]:
    """ECB 일일 기준환율에서 USD/KRW 환산값을 가져옵니다."""
    resp = requests.get(ECB_EXCHANGE_RATES_URL, timeout=30)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    cube_with_time = root.find(".//{*}Cube[@time]")
    if cube_with_time is None:
        raise ValueError("ECB exchange rate payload does not include a dated Cube node")

    rates: dict[str, float] = {}
    for entry in cube_with_time.findall("{*}Cube"):
        currency = entry.attrib.get("currency")
        rate = entry.attrib.get("rate")
        if currency and rate:
            rates[currency] = float(rate)

    usd_per_eur = rates.get("USD")
    krw_per_eur = rates.get("KRW")
    if not usd_per_eur or not krw_per_eur:
        raise ValueError("ECB daily rates do not include both USD and KRW")

    usd_to_krw = krw_per_eur / usd_per_eur
    return {
        "base": "USD",
        "quote": "KRW",
        "rate": round(usd_to_krw, 4),
        "reference_date": cube_with_time.attrib["time"],
        "source": "ECB reference rates",
    }


def main():
    args = parse_args()
    only_ids = parse_only_ids(args.only)

    # ebay.key 파일이 있으면 환경변수로 로드
    load_env_file(PROJECT_ROOT / "ebay.key")

    client_id = os.environ.get("EBAY_CLIENT_ID")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET")

    if not client_id or not client_secret:
        log.error("EBAY_CLIENT_ID and EBAY_CLIENT_SECRET environment variables required")
        log.error("Set them or create ebay.key file in project root")
        sys.exit(1)

    sandbox = args.sandbox or "SBX" in client_id
    if sandbox:
        log.info("Using eBay SANDBOX environment")

    auth_url, browse_url = get_ebay_urls(sandbox)

    # 디렉토리 생성
    (DATA_DIR / "products").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "daily").mkdir(parents=True, exist_ok=True)

    catalog_config = load_catalog()
    existing_catalog = load_existing_catalog_output()
    existing_products = {}
    exchange_rate = None
    if existing_catalog:
        exchange_rate = existing_catalog.get("exchange_rate")
        for category in existing_catalog.get("categories", []):
            for product in category.get("products", []):
                existing_products[product["id"]] = product

    token = get_access_token(client_id, client_secret, auth_url)

    openrouter_key = load_openrouter_key()
    if openrouter_key:
        log.info("OpenRouter API key loaded, LLM filtering enabled (%s)", _openrouter_model())
    else:
        log.info("No OpenRouter API key found, LLM filtering disabled")

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log.info("Fetching prices for %s", today)

    try:
        exchange_rate = fetch_usd_krw_exchange_rate()
        log.info(
            "Loaded exchange rate: USD 1 = KRW %.2f (ECB %s)",
            exchange_rate["rate"],
            exchange_rate["reference_date"],
        )
    except Exception as exc:
        if exchange_rate:
            log.warning("Exchange rate refresh failed (%s), keeping existing rate", exc)
        else:
            exchange_rate = _recover_exchange_rate_from_daily()
            if exchange_rate:
                log.warning(
                    "Exchange rate refresh failed (%s), recovered from daily snapshot (%.2f)",
                    exc, exchange_rate["rate"],
                )
            else:
                log.warning("Exchange rate refresh failed (%s), KRW conversion will be unavailable", exc)

    catalog_output = {
        "updated": today,
        "exchange_rate": exchange_rate,
        "categories": [],
    }
    daily_snapshot = load_daily_snapshot_for_date(today) if only_ids else {
        "date": today,
        "products": {},
    }
    daily_snapshot["exchange_rate"] = exchange_rate

    total_products = sum(
        1
        for cat in catalog_config["categories"]
        for product in cat["products"]
        if not only_ids or product["id"] in only_ids
    )
    processed = 0

    for category in catalog_config["categories"]:
        cat_entry = {
            "id": category["id"],
            "name_ko": category["name_ko"],
            "name_en": category["name_en"],
            "subcategories": category.get("subcategories", []),
            "products": [],
        }

        for product in category["products"]:
            pid = product["id"]
            if only_ids and pid not in only_ids:
                existing = existing_products.get(pid)
                if existing:
                    cat_entry["products"].append(existing)
                else:
                    empty_entry = build_base_product_entry(product)
                    empty_entry.update({
                        "median": None,
                        "mean": None,
                        "min": None,
                        "max": None,
                        "q1": None,
                        "q3": None,
                        "count": 0,
                        "count_filtered": 0,
                        "samples": [],
                    })
                    cat_entry["products"].append(empty_entry)
                continue

            processed += 1
            log.info(
                "[%d/%d] Fetching: %s (%s)",
                processed, total_products, pid, product["query"],
            )

            try:
                items, effective_max_price = search_items_for_product(
                    token,
                    browse_url,
                    product,
                )
                if effective_max_price != product["max_price"]:
                    log.info(
                        "  Using expanded search max: $%s -> $%s",
                        product["max_price"],
                        effective_max_price,
                    )

                if openrouter_key and items:
                    pre_count = len(items)
                    items = filter_items_with_llm(items, product, openrouter_key)
                    log.info(
                        "  LLM filtered: %d → %d items", pre_count, len(items)
                    )

                prices = collect_prices(items)

                stats = compute_stats(prices)
                samples = extract_sample_listings(items)

                product_entry = build_base_product_entry(product)
                product_entry.update(stats)
                product_entry["samples"] = samples
                cat_entry["products"].append(product_entry)

                daily_snapshot["products"][pid] = stats
                update_product_history(pid, today, stats)

                log.info(
                    "  → %d listings, median=$%s",
                    stats["count"],
                    stats["median"],
                )

            except requests.exceptions.RequestException as e:
                log.error("  → Request error for %s: %s", pid, e)
                error_entry = build_base_product_entry(product)
                error_entry.update({
                    "median": None,
                    "mean": None,
                    "min": None,
                    "max": None,
                    "q1": None,
                    "q3": None,
                    "count": 0,
                    "count_filtered": 0,
                    "samples": [],
                    "error": str(e),
                })
                cat_entry["products"].append(error_entry)

            # API 부하 방지
            time.sleep(0.3)

        catalog_output["categories"].append(cat_entry)

    # catalog.json 저장
    catalog_path = DATA_DIR / "catalog.json"
    with open(catalog_path, "w", encoding="utf-8") as f:
        json.dump(catalog_output, f, ensure_ascii=False, indent=1)
    log.info("Saved %s", catalog_path)

    # daily snapshot 저장
    daily_path = DATA_DIR / "daily" / f"{today}.json"
    with open(daily_path, "w", encoding="utf-8") as f:
        json.dump(daily_snapshot, f, ensure_ascii=False, indent=1)
    log.info("Saved %s", daily_path)

    # 오래된 스냅샷 정리
    cleanup_daily_snapshots()

    log.info("Done! Processed %d products.", processed)


if __name__ == "__main__":
    main()
