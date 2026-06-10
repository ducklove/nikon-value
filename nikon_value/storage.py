"""카탈로그 설정·일별 스냅샷·제품 히스토리 JSON 입출력."""

from __future__ import annotations

import json
import logging

import yaml

from nikon_value.paths import CONFIG_PATH, DATA_DIR

log = logging.getLogger(__name__)

MAX_DAILY_SNAPSHOTS = 400
MAX_PRODUCT_HISTORY = 365


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
