"""시세 수집 파이프라인 오케스트레이션 (CLI 진입점)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime

import requests

from nikon_value.ebay import collect_prices, get_access_token, get_ebay_urls, search_items_for_product
from nikon_value.env import load_env_file
from nikon_value.exchange import _recover_exchange_rate_from_daily, fetch_usd_krw_exchange_rate
from nikon_value.llm import _openrouter_model, filter_items_with_llm, load_openrouter_key
from nikon_value.paths import DATA_DIR, PROJECT_ROOT
from nikon_value.stats import compute_stats, extract_sample_listings
from nikon_value.storage import (
    build_base_product_entry,
    cleanup_daily_snapshots,
    load_catalog,
    load_daily_snapshot_for_date,
    load_existing_catalog_output,
    update_product_history,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


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
