"""시세 수집 파이프라인 오케스트레이션 (CLI 진입점).

책임을 네 단계로 나눈다:

1. :func:`build_run_context` — 자격증명·토큰·환율·카탈로그를 모은 실행 컨텍스트
2. :func:`process_product` — 제품 1개 처리(검색 → LLM → 통계 → 샘플 → 딜 → 히스토리)
3. :func:`write_outputs` — catalog.json / 환율 시계열 / 되먹임 / 캐시 / 계측 저장
4. :func:`main` — 위를 엮는 얇은 오케스트레이터

제품 1개의 실패는 :func:`process_product_safely`가 격리해 나머지 수집을 살리고,
실패가 임계값을 넘으면 :func:`enforce_failure_threshold`가 종료 코드로 드러낸다.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import requests

from nikon_value.deals import extract_deal_listings
from nikon_value.diagnostics import PROBE_MIN_ZERO_STREAK, diagnose_empty_products
from nikon_value.ebay import collect_prices, get_access_token, get_ebay_urls, search_items_for_product
from nikon_value.env import load_env_file
from nikon_value.exchange import (
    append_exchange_rate,
    fetch_usd_krw_exchange_rate,
    recover_exchange_rate_from_history,
)
from nikon_value.llm import _openrouter_model, filter_items_with_llm, load_openrouter_key
from nikon_value.llm_cache import LlmDecisionCache
from nikon_value.metrics import RunMetrics, append_run_metrics, reset_metrics
from nikon_value.paths import DATA_DIR, PROJECT_ROOT
from nikon_value.stats import compute_stats, extract_sample_listings
from nikon_value.storage import (
    ZERO_RESULT_RETRY_INTERVAL_DAYS,
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

# 제품 1개의 실패는 격리하지만, 전 제품이 무너지는 상황(토큰 만료, eBay 전면
# 장애)까지 조용히 삼키면 "거의 빈 catalog.json"이 성공으로 위장돼 커밋된다.
# 처리한 제품 중 이 비율을 초과해 실패하면 종료 코드 1로 표면화한다.
PRODUCT_FAILURE_RATE_THRESHOLD = 0.2

# 실패·미수집 제품에 기록하는 빈 통계.
EMPTY_STATS = {
    "median": None,
    "mean": None,
    "min": None,
    "max": None,
    "q1": None,
    "q3": None,
    "count": 0,
    "count_filtered": 0,
}

# API 부하 방지용 제품 간 간격(초).
PRODUCT_REQUEST_INTERVAL = 0.3


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
    parser.add_argument(
        "--no-diagnostics",
        action="store_true",
        help="0건 제품 진단 프로브를 건너뜁니다(API 호출을 한 건도 늘리지 않음)",
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


# --- 실행 컨텍스트 ------------------------------------------------------


@dataclass
class RunContext:
    """한 번의 수집 실행에서 공유되는 자원 묶음."""

    token: str
    browse_url: str
    catalog_config: dict
    today: str
    metrics: RunMetrics
    existing_products: dict[str, dict] = field(default_factory=dict)
    exchange_rate: dict | None = None
    openrouter_key: str | None = None
    llm_cache: LlmDecisionCache | None = None
    only_ids: set[str] = field(default_factory=set)
    diagnostics_enabled: bool = True
    # 이번 실행에서 0건이 나온 제품 설정. 수집이 끝난 뒤 진단 프로브 후보가 된다.
    empty_products: list[dict] = field(default_factory=list)


@dataclass
class ProductOutcome:
    """제품 1개 처리 결과."""

    entry: dict
    max_price_update: float | None = None
    failed: bool = False


def load_ebay_credentials() -> tuple[str, str]:
    """eBay 자격증명을 환경변수에서 읽습니다. 없으면 즉시 종료합니다."""
    client_id = os.environ.get("EBAY_CLIENT_ID")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET")

    if not client_id or not client_secret:
        log.error("EBAY_CLIENT_ID and EBAY_CLIENT_SECRET environment variables required")
        log.error("Set them or create ebay.key file in project root")
        sys.exit(1)
    return client_id, client_secret


def load_existing_products() -> tuple[dict[str, dict], dict | None]:
    """기존 catalog.json에서 제품 항목과 환율을 꺼냅니다.

    제품 항목은 `--only` 부분 수집에서 대상이 아닌 제품을 재사용하는 데,
    환율은 ECB 조회 실패 시 폴백 1단계(기존 값 유지)에 쓰인다.
    """
    existing_catalog = load_existing_catalog_output()
    if not existing_catalog:
        return {}, None

    existing_products = {
        product["id"]: product
        for category in existing_catalog.get("categories", [])
        for product in category.get("products", [])
    }
    return existing_products, existing_catalog.get("exchange_rate")


def _fallback_exchange_rate(existing_rate: dict | None, exc: Exception) -> dict | None:
    """환율 폴백 2·3단계: 기존 catalog 값 유지 → 최근 기록 복구."""
    if existing_rate:
        log.warning("Exchange rate refresh failed (%s), keeping existing rate", exc)
        return existing_rate

    recovered = recover_exchange_rate_from_history()
    if recovered:
        log.warning(
            "Exchange rate refresh failed (%s), recovered from exchange rate history (%.2f)",
            exc, recovered["rate"],
        )
        return recovered

    log.warning("Exchange rate refresh failed (%s), KRW conversion will be unavailable", exc)
    return None


def resolve_exchange_rate(existing_rate: dict | None) -> dict | None:
    """환율 3단계 폴백: ECB 조회 → 기존 catalog 값 유지 → 최근 기록 복구."""
    try:
        exchange_rate = fetch_usd_krw_exchange_rate()
        log.info(
            "Loaded exchange rate: USD 1 = KRW %.2f (ECB %s)",
            exchange_rate["rate"],
            exchange_rate["reference_date"],
        )
        return exchange_rate
    except Exception as exc:
        return _fallback_exchange_rate(existing_rate, exc)


def load_llm_resources() -> tuple[str | None, LlmDecisionCache | None]:
    """OpenRouter 키와 LLM 판정 캐시를 준비합니다. 키가 없으면 (None, None)."""
    openrouter_key = load_openrouter_key()
    if not openrouter_key:
        log.info("No OpenRouter API key found, LLM filtering disabled")
        return None, None

    log.info("OpenRouter API key loaded, LLM filtering enabled (%s)", _openrouter_model())
    llm_cache = LlmDecisionCache.load()
    log.info("LLM decision cache loaded: %d entries", llm_cache.entry_count())
    return openrouter_key, llm_cache


def build_run_context(args: argparse.Namespace) -> RunContext:
    """자격증명·토큰·환율·카탈로그를 모아 실행 컨텍스트를 만듭니다."""
    metrics = reset_metrics()

    # ebay.key 파일이 있으면 환경변수로 로드
    load_env_file(PROJECT_ROOT / "ebay.key")
    client_id, client_secret = load_ebay_credentials()

    sandbox = args.sandbox or "SBX" in client_id
    if sandbox:
        log.info("Using eBay SANDBOX environment")
    auth_url, browse_url = get_ebay_urls(sandbox)

    # 디렉토리 생성
    (DATA_DIR / "products").mkdir(parents=True, exist_ok=True)

    catalog_config = load_catalog()
    existing_products, existing_rate = load_existing_products()
    token = get_access_token(client_id, client_secret, auth_url)
    openrouter_key, llm_cache = load_llm_resources()

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    log.info("Fetching prices for %s", today)

    return RunContext(
        token=token,
        browse_url=browse_url,
        catalog_config=catalog_config,
        today=today,
        metrics=metrics,
        existing_products=existing_products,
        exchange_rate=resolve_exchange_rate(existing_rate),
        openrouter_key=openrouter_key,
        llm_cache=llm_cache,
        only_ids=parse_only_ids(args.only),
        diagnostics_enabled=not args.no_diagnostics,
    )


# --- 제품 1개 처리 ------------------------------------------------------


def build_empty_product_entry(product: dict, error: str | None = None) -> dict:
    """빈 통계 제품 항목. 실패한 제품은 `error` 필드로 원인을 남긴다."""
    entry = build_base_product_entry(product)
    entry.update(EMPTY_STATS)
    entry["samples"] = []
    if error is not None:
        entry["error"] = error
    return entry


def search_product_items(ctx: RunContext, product: dict) -> tuple[list[dict], float]:
    """연속 0건 이력을 반영해 제품을 검색합니다."""
    # 연속 0건이 이어진 제품은 상한 문제가 아니라 매물이 없는 것이므로
    # 빈 결과만으로는 확장하지 않는다. 매물이 다시 나타나면 즉시 복귀.
    #
    # 다만 "0건이라 확장 안 함"이 그대로 굳으면, 시세가 max_price 위로 올라간
    # 제품은 기본 검색이 영원히 0건이라 자력 회복이 불가능해진다. 그래서
    # ZERO_RESULT_RETRY_INTERVAL_DAYS마다 한 번은 확장을 재시도해 탈출구를 둔다.
    streak = zero_result_streak(product["id"])
    periodic_retry = (
        streak >= ZERO_RESULT_STREAK_THRESHOLD
        and streak % ZERO_RESULT_RETRY_INTERVAL_DAYS == 0
    )
    expand_when_empty = streak < ZERO_RESULT_STREAK_THRESHOLD or periodic_retry
    if periodic_retry:
        log.info(
            "  Retrying empty-result expansion despite %d consecutive zero-result days",
            streak,
        )
    elif not expand_when_empty:
        log.info(
            "  Skipping empty-result expansion (%d consecutive zero-result runs)",
            streak,
        )

    return search_items_for_product(
        ctx.token,
        ctx.browse_url,
        product,
        expand_when_empty=expand_when_empty,
        metrics=ctx.metrics,
    )


def apply_llm_filter(ctx: RunContext, product: dict, items: list[dict]) -> list[dict]:
    """LLM 보조 필터를 적용합니다(키가 없거나 매물이 없으면 그대로)."""
    if not ctx.openrouter_key or not items:
        return items

    pre_count = len(items)
    filtered = filter_items_with_llm(
        items, product, ctx.openrouter_key, cache=ctx.llm_cache, metrics=ctx.metrics
    )
    log.info("  LLM filtered: %d → %d items", pre_count, len(filtered))
    return filtered


def process_product(ctx: RunContext, product: dict) -> ProductOutcome:
    """제품 1개를 처리합니다: 검색 → 필터 → 통계 → 샘플 → 딜 → 히스토리 갱신."""
    pid = product["id"]
    items, effective_max_price = search_product_items(ctx, product)

    max_price_update = None
    if effective_max_price != product["max_price"]:
        log.info(
            "  Using expanded search max: $%s -> $%s",
            product["max_price"],
            effective_max_price,
        )
        # 다음 실행에서 같은 확장을 반복하지 않도록 설정에 되먹인다.
        max_price_update = effective_max_price

    items = apply_llm_filter(ctx, product, items)

    stats = compute_stats(collect_prices(items))
    entry = build_base_product_entry(product)
    entry.update(stats)
    entry["samples"] = extract_sample_listings(items)
    entry["deals"] = extract_deal_listings(items, stats["median"])

    update_product_history(pid, ctx.today, stats)
    log.info("  → %d listings, median=$%s", stats["count"], stats["median"])

    return ProductOutcome(entry=entry, max_price_update=max_price_update)


def process_product_safely(ctx: RunContext, product: dict) -> ProductOutcome:
    """제품 1개의 실패를 격리합니다. 실패해도 나머지 제품 수집은 계속된다.

    `KeyboardInterrupt`/`SystemExit`은 `BaseException`이므로 여기서 잡히지 않고
    그대로 전파된다(중단 요청은 삼키지 않는다).
    """
    try:
        return process_product(ctx, product)
    except Exception as exc:
        # 네트워크 오류는 흔하므로 한 줄로, 그 밖의 예외(예: extract_price의
        # float 변환 실패)는 원인을 찾을 수 있도록 트레이스백까지 남긴다.
        unexpected = not isinstance(exc, requests.exceptions.RequestException)
        log.error(
            "  → Failed for %s: %s: %s",
            product["id"], type(exc).__name__, exc,
            exc_info=unexpected,
        )
        ctx.metrics.record_product_failure()
        return ProductOutcome(
            entry=build_empty_product_entry(product, error=str(exc)),
            failed=True,
        )


# --- 카탈로그 순회 ------------------------------------------------------


def count_target_products(ctx: RunContext) -> int:
    """이번 실행에서 실제로 수집할 제품 수(--only 반영)."""
    return sum(
        1
        for category in ctx.catalog_config["categories"]
        for product in category["products"]
        if not ctx.only_ids or product["id"] in ctx.only_ids
    )


def reuse_existing_entry(ctx: RunContext, product: dict) -> dict:
    """--only 대상이 아닌 제품은 기존 catalog 항목을 그대로 재사용합니다."""
    return ctx.existing_products.get(product["id"]) or build_empty_product_entry(product)


def collect_categories(ctx: RunContext) -> tuple[list[dict], dict[str, float]]:
    """카탈로그를 순회하며 카테고리별 제품 항목과 상한 되먹임을 모읍니다."""
    total_products = count_target_products(ctx)
    processed = 0
    categories: list[dict] = []
    max_price_updates: dict[str, float] = {}

    for category in ctx.catalog_config["categories"]:
        cat_entry = {
            "id": category["id"],
            "name_ko": category["name_ko"],
            "name_en": category["name_en"],
            "subcategories": category.get("subcategories", []),
            "products": [],
        }

        for product in category["products"]:
            pid = product["id"]
            if ctx.only_ids and pid not in ctx.only_ids:
                cat_entry["products"].append(reuse_existing_entry(ctx, product))
                continue

            processed += 1
            ctx.metrics.record_product()
            log.info(
                "[%d/%d] Fetching: %s (%s)",
                processed, total_products, pid, product["query"],
            )

            outcome = process_product_safely(ctx, product)
            cat_entry["products"].append(outcome.entry)
            if outcome.max_price_update is not None:
                max_price_updates[pid] = outcome.max_price_update
            # 0건 제품은 수집이 끝난 뒤 한 번에 진단한다. 실패한 제품은
            # 원인이 예외로 이미 드러나 있으므로 후보에서 뺀다.
            if not outcome.failed and not outcome.entry.get("count"):
                ctx.empty_products.append(product)

            # API 부하 방지
            time.sleep(PRODUCT_REQUEST_INTERVAL)

        categories.append(cat_entry)

    return categories, max_price_updates


# --- 0건 제품 진단 ------------------------------------------------------


def run_empty_product_diagnostics(ctx: RunContext) -> None:
    """0건 제품에 한해 제약 완화 프로브를 돌려 원인을 기록합니다.

    진단 전용이다. 결과는 `data/empty-product-report.json`에만 쌓이고
    catalog.json·제품 히스토리에는 한 글자도 들어가지 않는다. 계측에 프로브
    호출을 포함시키려고 `write_outputs` 직전에 돌리며, 진단이 실패해도 수집
    결과를 무효로 만들지 않도록 예외를 통째로 격리한다.
    """
    if not ctx.diagnostics_enabled:
        log.info("Empty-product diagnostics disabled (--no-diagnostics)")
        return
    if not ctx.empty_products:
        return

    candidates = [
        product for product in ctx.empty_products
        if zero_result_streak(product["id"]) >= PROBE_MIN_ZERO_STREAK
    ]
    if not candidates:
        log.info(
            "Empty-product diagnostics: %d zero-result products, none past the %d-run streak",
            len(ctx.empty_products), PROBE_MIN_ZERO_STREAK,
        )
        return

    zero_streaks = {p["id"]: zero_result_streak(p["id"]) for p in candidates}
    try:
        diagnose_empty_products(
            ctx.token,
            ctx.browse_url,
            candidates,
            ctx.today,
            zero_streaks,
            metrics=ctx.metrics,
            path=DATA_DIR / "empty-product-report.json",
        )
    except Exception as exc:
        log.error("Empty-product diagnostics failed: %s: %s", type(exc).__name__, exc)


# --- 결과 저장 ----------------------------------------------------------


def write_outputs(
    ctx: RunContext,
    catalog_output: dict,
    max_price_updates: dict[str, float],
) -> None:
    """catalog.json · 환율 시계열 · 상한 되먹임 · LLM 캐시 · 계측을 저장합니다."""
    # catalog.json 저장
    catalog_path = DATA_DIR / "catalog.json"
    with open(catalog_path, "w", encoding="utf-8") as f:
        json.dump(catalog_output, f, ensure_ascii=False, indent=1)
    log.info("Saved %s", catalog_path)

    # 환율 시계열 append (제품 통계는 data/products/에 이미 있으므로 중복 저장하지 않는다)
    if append_exchange_rate(ctx.exchange_rate, ctx.today):
        log.info("Appended exchange rate for %s", ctx.today)

    # 적응형 상한 되먹임: 수집 종료 시 한 번에 저장한다
    if max_price_updates:
        updated_ids = update_catalog_max_prices(max_price_updates)
        log.info(
            "max_price feedback: %d/%d products updated in config",
            len(updated_ids), len(max_price_updates),
        )

    # LLM 판정 캐시 저장
    if ctx.llm_cache is not None and ctx.llm_cache.dirty:
        ctx.llm_cache.save()
        log.info("Saved LLM decision cache: %d entries", ctx.llm_cache.entry_count())

    # 실행 계측 기록
    ctx.metrics.finish()
    log.info(ctx.metrics.summary_line())
    append_run_metrics(ctx.metrics)


def enforce_failure_threshold(metrics: RunMetrics) -> None:
    """실패율이 임계값을 넘으면 실행을 실패로 표면화합니다(종료 코드 1).

    제품별 격리는 "한 건의 실패가 나머지를 죽이지 않게" 하는 장치지 실패를
    숨기는 장치가 아니다. 토큰 만료·eBay 전면 장애처럼 전 제품이 무너지는
    상황은 워크플로 실패(→ 이슈 자동 생성)로 드러나야 한다. 결과 저장 뒤에
    호출하므로 부분 성공분과 계측은 이미 기록된 상태다.
    """
    if not metrics.products_failed:
        return

    rate = metrics.failure_rate()
    if rate <= PRODUCT_FAILURE_RATE_THRESHOLD:
        log.warning(
            "%d/%d products failed (%.1f%%) — isolated failures, continuing",
            metrics.products_failed, metrics.products_processed, rate * 100,
        )
        return

    log.error(
        "%d/%d products failed (%.1f%% > %.0f%% threshold) — treating this run as failed",
        metrics.products_failed, metrics.products_processed, rate * 100,
        PRODUCT_FAILURE_RATE_THRESHOLD * 100,
    )
    sys.exit(1)


def main():
    """수집 파이프라인을 실행합니다."""
    ctx = build_run_context(parse_args())

    categories, max_price_updates = collect_categories(ctx)
    catalog_output = {
        "updated": ctx.today,
        "exchange_rate": ctx.exchange_rate,
        "categories": categories,
    }

    run_empty_product_diagnostics(ctx)
    write_outputs(ctx, catalog_output, max_price_updates)
    enforce_failure_threshold(ctx.metrics)
    log.info("Done! Processed %d products.", ctx.metrics.products_processed)


if __name__ == "__main__":
    main()
