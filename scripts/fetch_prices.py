#!/usr/bin/env python3
"""eBay Browse API를 사용하여 니콘 중고 장비 시세를 수집합니다."""

import json
import logging
import os
import statistics
import sys
import time
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

MAX_DAILY_SNAPSHOTS = 400
MAX_PRODUCT_HISTORY = 365


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


def search_items(token: str, browse_url: str, query: str, category_id: str,
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
            "category_ids": category_id,
            "filter": ",".join([
                "conditionIds:{3000}",  # USED
                f"price:[{min_price}..{max_price}]",
                "priceCurrency:USD",
                "deliveryCountry:US",
                "buyingOptions:{FIXED_PRICE}",
            ]),
            "sort": "price",
            "limit": limit,
            "offset": offset,
            "fieldgroups": "MATCHING_ITEMS",
        }

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
    samples = []
    for item in items[:max_samples]:
        price = extract_price(item)
        if price is None:
            continue
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


def main():
    # ebay.key 파일이 있으면 환경변수로 로드
    load_env_file(PROJECT_ROOT / "ebay.key")

    client_id = os.environ.get("EBAY_CLIENT_ID")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET")

    if not client_id or not client_secret:
        log.error("EBAY_CLIENT_ID and EBAY_CLIENT_SECRET environment variables required")
        log.error("Set them or create ebay.key file in project root")
        sys.exit(1)

    sandbox = "--sandbox" in sys.argv or "SBX" in client_id
    if sandbox:
        log.info("Using eBay SANDBOX environment")

    auth_url, browse_url = get_ebay_urls(sandbox)

    # 디렉토리 생성
    (DATA_DIR / "products").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "daily").mkdir(parents=True, exist_ok=True)

    catalog_config = load_catalog()
    token = get_access_token(client_id, client_secret, auth_url)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("Fetching prices for %s", today)

    catalog_output = {
        "updated": today,
        "categories": [],
    }
    daily_snapshot = {
        "date": today,
        "products": {},
    }

    total_products = sum(
        len(cat["products"]) for cat in catalog_config["categories"]
    )
    processed = 0

    for category in catalog_config["categories"]:
        cat_entry = {
            "id": category["id"],
            "name_ko": category["name_ko"],
            "name_en": category["name_en"],
            "products": [],
        }

        for product in category["products"]:
            processed += 1
            pid = product["id"]
            log.info(
                "[%d/%d] Fetching: %s (%s)",
                processed, total_products, pid, product["query"],
            )

            try:
                items = search_items(
                    token,
                    browse_url,
                    product["query"],
                    product["category_id"],
                    product["min_price"],
                    product["max_price"],
                )

                prices = []
                for item in items:
                    p = extract_price(item)
                    if p is not None:
                        prices.append(p)

                stats = compute_stats(prices)
                samples = extract_sample_listings(items)

                product_entry = {
                    "id": pid,
                    "name_ko": product["name_ko"],
                    "name_en": product["name_en"],
                }
                if "release_year" in product:
                    product_entry["release_year"] = product["release_year"]
                if "focal_length_min" in product:
                    product_entry["focal_length_min"] = product["focal_length_min"]
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

            except requests.exceptions.HTTPError as e:
                log.error("  → HTTP error for %s: %s", pid, e)
                error_entry = {
                    "id": pid,
                    "name_ko": product["name_ko"],
                    "name_en": product["name_en"],
                }
                if "release_year" in product:
                    error_entry["release_year"] = product["release_year"]
                if "focal_length_min" in product:
                    error_entry["focal_length_min"] = product["focal_length_min"]
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
