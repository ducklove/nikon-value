#!/usr/bin/env python3
"""eBay Browse API를 사용하여 니콘 중고 장비 시세를 수집합니다."""

import argparse
import json
import logging
import os
import re
import statistics
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
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

GEMINI_DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"


def _gemini_api_url() -> str:
    """환경변수 GEMINI_MODEL로 모델을 지정할 수 있습니다."""
    model = os.environ.get("GEMINI_MODEL", GEMINI_DEFAULT_MODEL)
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ECB_EXCHANGE_RATES_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

MAX_DAILY_SNAPSHOTS = 400
MAX_PRODUCT_HISTORY = 365

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


def load_gemini_key() -> str | None:
    """gemini.key 파일 또는 GEMINI_API_KEY 환경변수에서 API 키를 로드합니다."""
    key_file = PROJECT_ROOT / "gemini.key"
    if key_file.exists():
        with open(key_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    _, value = line.split("=", 1)
                    return value.strip()
                return line
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    return None


def filter_items_with_llm(
    items: list[dict], product: dict, gemini_key: str
) -> list[dict]:
    """Gemini API를 사용하여 리스팅 타이틀이 실제 해당 제품인지 검증합니다."""
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
        "Below are eBay listing titles. Return a JSON array of indices "
        "(0-based) for listings that ARE actually selling this specific product.\n\n"
        "Exclude:\n"
        + "\n".join(exclude_lines)
        + "\n"
        + ("- Lens-only listings when the product is a camera body\n" if not is_accessory else "")
        + "\n"
        f"Listings:\n{listings_text}"
    )

    try:
        resp = requests.post(
            _gemini_api_url(),
            params={"key": gemini_key},
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                },
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        indices = json.loads(text)

        if not isinstance(indices, list):
            log.warning("  LLM returned non-list, skipping filter")
            return items

        valid_indices = [i for i in indices if isinstance(i, int) and 0 <= i < len(items)]
        filtered = [items[i] for i in valid_indices]

        if not filtered:
            if len(items) <= 5:
                log.info("  LLM filtered all %d items — accepting (small set)", len(items))
                return []
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
        "X-EBAY-C-ENDUSERCTX": "affiliateCampaignId=<ePNCampaignId>,affiliateReferenceId=<referenceId>",
    }

    all_items = []
    offset = 0
    limit = 200

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
            log.warning("Rate limited, waiting 5 seconds...")
            time.sleep(5)
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
        samples.append({
            "title": item.get("title", ""),
            "price": price,
            "currency": item.get("price", {}).get("currency", "USD"),
            "condition": item.get("condition", ""),
            "image": item.get("thumbnailImages", [{}])[0].get("imageUrl", ""),
            "url": item.get("itemWebUrl", ""),
        })
    return samples


def load_catalog() -> dict:
    """products.yaml를 로드합니다."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_existing_catalog_output() -> dict | None:
    """기존 catalog.json을 로드합니다."""
    catalog_path = DATA_DIR / "catalog.json"
    if not catalog_path.exists():
        return None
    with open(catalog_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_daily_snapshot_for_date(date_str: str) -> dict:
    """특정 날짜의 기존 일별 스냅샷을 로드합니다."""
    filepath = DATA_DIR / "daily" / f"{date_str}.json"
    if not filepath.exists():
        return {"date": date_str, "products": {}}
    with open(filepath, "r", encoding="utf-8") as f:
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
        with open(filepath, "r", encoding="utf-8") as f:
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
    with open(path, "r") as f:
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

    gemini_key = load_gemini_key()
    if gemini_key:
        log.info("Gemini API key loaded, LLM filtering enabled")
    else:
        log.info("No Gemini API key found, LLM filtering disabled")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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
                items = search_items(
                    token,
                    browse_url,
                    product["query"],
                    product.get("search_category_id", product["category_id"]),
                    product["min_price"],
                    product["max_price"],
                )
                items = filter_items_with_rules(items, product)

                if gemini_key and items:
                    pre_count = len(items)
                    items = filter_items_with_llm(items, product, gemini_key)
                    log.info(
                        "  LLM filtered: %d → %d items", pre_count, len(items)
                    )

                prices = []
                for item in items:
                    p = extract_price(item)
                    if p is not None:
                        prices.append(p)

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
