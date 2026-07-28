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

from nikon_value.deals import extract_deal_listings
from nikon_value.ebay import collect_prices, get_access_token, get_ebay_urls, search_items_for_product
from nikon_value.env import load_env_file
from nikon_value.exchange import (
    append_exchange_rate,
    fetch_usd_krw_exchange_rate,
    recover_exchange_rate_from_history,
)
from nikon_value.llm import _openrouter_model, filter_items_with_llm, load_openrouter_key
from nikon_value.llm_cache import LlmDecisionCache
from nikon_value.metrics import append_run_metrics, reset_metrics
from nikon_value.paths import DATA_DIR, PROJECT_ROOT
from nikon_value.stats import compute_stats, extract_sample_listings
from nikon_value.storage import (
    ZERO_RESULT_STREAK_THRESHOLD,
    build_base_product_entry,
    load_catalog,
    load_existing_catalog_output,
    update_catalog_max_prices,
    update_product_history,
    zero_result_streak,
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
    metrics = reset_metrics()

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
    llm_cache = None
    if openrouter_key:
        log.info("OpenRouter API key loaded, LLM filtering enabled (%s)", _openrouter_model())
        llm_cache = LlmDecisionCache.load()
        log.info("LLM decision cache loaded: %d entries", llm_cache.entry_count())
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
            exchange_rate = recover_exchange_rate_from_history()
            if exchange_rate:
                log.warning(
                    "Exchange rate refresh failed (%s), recovered from exchange rate history (%.2f)",
                    exc, exchange_rate["rate"],
                )
            else:
                log.warning("Exchange rate refresh failed (%s), KRW conversion will be unavailable", exc)

    catalog_output = {
        "updated": today,
        "exchange_rate": exchange_rate,
        "categories": [],
    }
    max_price_updates: dict[str, float] = {}

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
            metrics.record_product()
            log.info(
                "[%d/%d] Fetching: %s (%s)",
                processed, total_products, pid, product["query"],
            )

            try:
                # 연속 0건이 이어진 제품은 상한 문제가 아니라 매물이 없는 것이므로
                # 빈 결과만으로는 확장하지 않는다. 매물이 다시 나타나면 즉시 복귀.
                streak = zero_result_streak(pid)
                expand_when_empty = streak < ZERO_RESULT_STREAK_THRESHOLD
                if not expand_when_empty:
                    log.info(
                        "  Skipping empty-result expansion (%d consecutive zero-result runs)",
                        streak,
                    )

                items, effective_max_price = search_items_for_product(
                    token,
                    browse_url,
                    product,
                    expand_when_empty=expand_when_empty,
                    metrics=metrics,
                )
                if effective_max_price != product["max_price"]:
                    log.info(
                        "  Using expanded search max: $%s -> $%s",
                        product["max_price"],
                        effective_max_price,
                    )
                    # 다음 실행에서 같은 확장을 반복하지 않도록 설정에 되먹인다.
                    max_price_updates[pid] = effective_max_price

                if openrouter_key and items:
                    pre_count = len(items)
                    items = filter_items_with_llm(
                        items, product, openrouter_key, cache=llm_cache, metrics=metrics
                    )
                    log.info(
                        "  LLM filtered: %d → %d items", pre_count, len(items)
                    )

                prices = collect_prices(items)

                stats = compute_stats(prices)
                samples = extract_sample_listings(items)
                deals = extract_deal_listings(items, stats["median"])

                product_entry = build_base_product_entry(product)
                product_entry.update(stats)
                product_entry["samples"] = samples
                product_entry["deals"] = deals
                cat_entry["products"].append(product_entry)

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

    # 환율 시계열 append (제품 통계는 data/products/에 이미 있으므로 중복 저장하지 않는다)
    if append_exchange_rate(exchange_rate, today):
        log.info("Appended exchange rate for %s", today)

    # 적응형 상한 되먹임: 수집 종료 시 한 번에 저장한다
    if max_price_updates:
        updated_ids = update_catalog_max_prices(max_price_updates)
        log.info(
            "max_price feedback: %d/%d products updated in config",
            len(updated_ids), len(max_price_updates),
        )

    # LLM 판정 캐시 저장
    if llm_cache is not None and llm_cache.dirty:
        llm_cache.save()
        log.info("Saved LLM decision cache: %d entries", llm_cache.entry_count())

    # 실행 계측 기록
    metrics.finish()
    log.info(metrics.summary_line())
    append_run_metrics(metrics)

    log.info("Done! Processed %d products.", processed)


if __name__ == "__main__":
    main()
