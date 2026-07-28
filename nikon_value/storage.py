"""카탈로그 설정·제품 히스토리 JSON 입출력과 products.yaml 되먹임."""

from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path

import yaml

from nikon_value.paths import CONFIG_PATH, DATA_DIR

log = logging.getLogger(__name__)

MAX_PRODUCT_HISTORY = 365
# 이 횟수만큼 연속으로 0건이면 "상한이 낮아서"가 아니라 eBay에 매물이 없는
# 것으로 보고 적응형 상한 확장을 건너뛴다.
ZERO_RESULT_STREAK_THRESHOLD = 3

_PRODUCT_ID_RE = re.compile(r"^(\s*)-\s+id:\s*(\S+)\s*$")
_MAX_PRICE_RE = re.compile(r"^(\s*)max_price:\s*(\S+)\s*$")


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


def iter_config_products(catalog_config: dict):
    """설정에 정의된 모든 제품을 순회합니다."""
    for category in catalog_config.get("categories", []) or []:
        yield from category.get("products", []) or []


def _config_fingerprint(catalog_config: dict) -> dict:
    """max_price를 제외한 설정 구조의 지문(되먹임 안전장치용)."""
    clone = copy.deepcopy(catalog_config)
    for product in iter_config_products(clone):
        product.pop("max_price", None)
    return clone


def _format_max_price(value: float) -> str:
    """YAML에 쓸 max_price 표기(정수는 정수로)."""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return repr(round(number, 2))


def update_catalog_max_prices(updates: dict[str, float], path: Path = CONFIG_PATH) -> list[str]:
    """적응형 탐색이 찾아낸 상한을 products.yaml에 되먹입니다.

    yaml.safe_dump로 통째로 재직렬화하면 주석·순서·구조가 날아가므로
    해당 `max_price:` 라인만 라인 단위로 치환한다. 저장 전후 파일을 파싱해
    제품 수·ID 집합·max_price 외 필드가 완전히 동일한지 검증하고, 하나라도
    어긋나면 아무것도 쓰지 않는다.
    """
    if not updates:
        return []

    original_text = path.read_text(encoding="utf-8")
    before = yaml.safe_load(original_text)

    lines = original_text.splitlines(keepends=True)
    current_id: str | None = None
    applied: dict[str, float] = {}

    for index, line in enumerate(lines):
        stripped = line.rstrip("\r\n")

        id_match = _PRODUCT_ID_RE.match(stripped)
        if id_match:
            current_id = id_match.group(2).strip("'\"")
            continue

        price_match = _MAX_PRICE_RE.match(stripped)
        if price_match and current_id in updates:
            newline = line[len(stripped):]
            lines[index] = f"{price_match.group(1)}max_price: {_format_max_price(updates[current_id])}{newline}"
            applied[current_id] = updates[current_id]
            current_id = None  # 제품당 한 번만 치환한다

    missing = sorted(set(updates) - set(applied))
    if missing:
        log.warning("max_price feedback: no config line found for %s", ", ".join(missing))

    if not applied:
        return []

    new_text = "".join(lines)
    try:
        after = yaml.safe_load(new_text)
    except yaml.YAMLError as exc:
        log.error("max_price feedback aborted: rewritten config does not parse (%s)", exc)
        return []

    before_products = list(iter_config_products(before))
    after_products = list(iter_config_products(after))
    before_ids = [p.get("id") for p in before_products]
    after_ids = [p.get("id") for p in after_products]

    if len(before_products) != len(after_products) or before_ids != after_ids:
        log.error(
            "max_price feedback aborted: product set changed (%d -> %d)",
            len(before_products), len(after_products),
        )
        return []

    if _config_fingerprint(before) != _config_fingerprint(after):
        log.error("max_price feedback aborted: fields other than max_price changed")
        return []

    before_by_id = {p.get("id"): p for p in before_products}
    after_by_id = {p.get("id"): p for p in after_products}
    for product_id, expected in applied.items():
        actual = after_by_id.get(product_id, {}).get("max_price")
        if actual is None or float(actual) != float(expected):
            log.error(
                "max_price feedback aborted: %s expected %s but parsed %s",
                product_id, expected, actual,
            )
            return []

    # 되먹임 대상이 아닌 제품의 max_price는 한 건도 움직이면 안 된다.
    for product_id, product in after_by_id.items():
        if product_id in applied:
            continue
        if product.get("max_price") != before_by_id.get(product_id, {}).get("max_price"):
            log.error("max_price feedback aborted: %s changed unexpectedly", product_id)
            return []

    path.write_text(new_text, encoding="utf-8")
    log.info("Updated max_price for %d products in %s", len(applied), path.name)
    return sorted(applied)


def load_product_history(product_id: str) -> list[dict]:
    """제품별 시계열 JSON을 로드합니다. 없거나 깨졌으면 빈 목록."""
    filepath = DATA_DIR / "products" / f"{product_id}.json"
    if not filepath.exists():
        return []
    try:
        history = json.loads(filepath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Product history unreadable for %s (%s)", product_id, exc)
        return []
    return history if isinstance(history, list) else []


def count_trailing_zero_results(history: list[dict]) -> int:
    """최근 항목부터 연속 0건 횟수를 셉니다. 매물이 있으면 즉시 0으로 복귀."""
    streak = 0
    for entry in reversed(history):
        if not isinstance(entry, dict) or (entry.get("count") or 0) > 0:
            break
        streak += 1
    return streak


def zero_result_streak(product_id: str) -> int:
    """제품의 최근 연속 0건 횟수를 히스토리에서 유도합니다."""
    return count_trailing_zero_results(load_product_history(product_id))


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
