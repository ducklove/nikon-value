#!/usr/bin/env python3
"""0건·저매물 제품을 오프라인 근거로 분류하고 프로브 리포트를 읽습니다.

eBay 자격증명 없이 동작한다. 쓰는 근거는 세 가지뿐이다:

1. `data/products/*.json` 히스토리 — 한 번이라도 매물이 잡힌 적이 있는가
2. `config/products.yaml` — 같은 카테고리에서 정상 동작하는 제품들과의 설정 차이
3. `data/empty-product-report.json` — 자격증명이 있는 정규 수집이 남긴 프로브 결과

사용법::

    python scripts/diagnose_empty_products.py              # 저매물 제품 분류표
    python scripts/diagnose_empty_products.py --report     # 프로브 리포트 요약
    python scripts/diagnose_empty_products.py --threshold 1.0 --window 30
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nikon_value.diagnostics import load_report  # noqa: E402
from nikon_value.storage import (  # noqa: E402
    count_trailing_zero_results,
    iter_config_products,
    load_catalog,
    load_product_history,
)

# 이 값 미만이면 "매물이 사실상 없다"고 본다(90일 평균 매물 수).
DEFAULT_THRESHOLD = 0.5
DEFAULT_WINDOW = 90


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    parser.add_argument("--report", action="store_true", help="프로브 리포트를 요약합니다")
    return parser.parse_args()


def average_count(product_id: str, window: int) -> float:
    """최근 `window`회 수집의 평균 매물 수."""
    counts = [entry.get("count") or 0 for entry in load_product_history(product_id)][-window:]
    return sum(counts) / len(counts) if counts else 0.0


def listing_days(product_id: str) -> tuple[int, int]:
    """(매물이 잡힌 수집 횟수, 전체 수집 횟수)."""
    counts = [entry.get("count") or 0 for entry in load_product_history(product_id)]
    return sum(1 for count in counts if count > 0), len(counts)


def category_of(catalog: dict, product_id: str) -> str:
    for category in catalog.get("categories", []):
        for product in category.get("products", []):
            if product["id"] == product_id:
                return category["id"]
    return "?"


def peer_summary(catalog: dict, category_id: str, window: int) -> str:
    """같은 카테고리 정상 제품들의 매물 수 중앙값(비교 기준선)."""
    values = [
        average_count(product["id"], window)
        for category in catalog.get("categories", [])
        if category["id"] == category_id
        for product in category.get("products", [])
    ]
    healthy = [value for value in values if value >= DEFAULT_THRESHOLD]
    if not healthy:
        return "-"
    return f"{statistics.median(healthy):.1f}"


def offline_hint(product: dict, ever_listed: bool) -> str:
    """오프라인에서만 말할 수 있는 1차 소견."""
    if ever_listed:
        return "설정은 동작함(과거 매물 포착) — 공급 자체가 얇음"
    query = product["query"]
    tokens = query.split()
    for index, token in enumerate(tokens):
        if token.startswith("-") and index + 1 < len(tokens) and not tokens[index + 1].startswith("-"):
            return f"q 구문 의심: `-`는 한 토큰만 제외한다 ({token} {tokens[index + 1]})"
    if query.count(" -") >= 5:
        return "제외어 과다 — 키트 동봉 매물이 통째로 빠질 수 있음"
    if len([t for t in tokens if not t.startswith("-")]) >= 6:
        return "필수 토큰 과다 — 공백은 AND라 매칭이 좁아짐"
    if product.get("is_rare"):
        return "희귀 등급 지정 제품 — 0건이 정답일 수 있음"
    return "오프라인 근거로는 판단 보류 — 프로브 필요"


def print_classification(threshold: float, window: int) -> None:
    catalog = load_catalog()
    rows = []
    for product in iter_config_products(catalog):
        average = average_count(product["id"], window)
        if average >= threshold:
            continue
        listed, total = listing_days(product["id"])
        rows.append((average, product, listed, total))

    rows.sort(key=lambda row: (row[0], row[1]["id"]))
    print(f"{len(rows)}개 제품이 최근 {window}회 평균 {threshold}건 미만입니다.\n")
    header = f"{'avg':>6}  {'product':38s} {'listed/total':>12}  {'streak':>6}  {'peer':>5}  hint"
    print(header)
    print("-" * len(header))
    for average, product, listed, total in rows:
        streak = count_trailing_zero_results(load_product_history(product["id"]))
        peer = peer_summary(catalog, category_of(catalog, product["id"]), window)
        print(
            f"{average:6.3f}  {product['id']:38s} {listed:5d}/{total:<6d} "
            f"{streak:6d}  {peer:>5}  {offline_hint(product, listed > 0)}"
        )


def print_report() -> None:
    report = load_report()
    products = report.get("products") or {}
    if not products:
        print(
            "프로브 리포트가 비어 있습니다. 자격증명이 있는 정규 수집이 한 번 돌아야"
            " data/empty-product-report.json이 생깁니다."
        )
        return

    print(f"프로브 리포트 (updated={report.get('updated')}, {len(products)}개 제품)\n")
    for pid, entry in sorted(products.items()):
        print(f"### {pid} — {entry.get('verdict')} ({entry.get('last_probed')})")
        print(f"    {entry.get('verdict_ko', '')}")
        for probe in entry.get("probes") or []:
            total = probe.get("total")
            mark = "?" if total is None else str(total)
            print(f"    {probe['name']:22s} {mark:>6}  {probe.get('description_ko', '')}")
            for title in probe.get("sample_titles") or []:
                print(f"        · {title}")
        print()


def main() -> None:
    args = parse_args()
    if args.report:
        print_report()
    else:
        print_classification(args.threshold, args.window)


if __name__ == "__main__":
    main()
