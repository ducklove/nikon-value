"""카탈로그·설정·히스토리 데이터 로딩과 머지/통계 헬퍼."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

import yaml

from nikon_value.paths import CONFIG_PATH, DATA_DIR

# PROJECT_ROOT는 기존 scripts.build_static_site 공개 API 호환을 위해 재노출한다.
from nikon_value.paths import PROJECT_ROOT as PROJECT_ROOT

CATALOG_PATH = DATA_DIR / 'catalog.json'
BODY_CATEGORIES = {'z-mount-bodies', 'f-mount-dslr', 'film-cameras'}
RARITY_FIELDS = (
    'is_rare',
    'rarity_tier',
    'rarity_sort',
    'rarity_price_hint',
    'rarity_note',
)


def load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding='utf-8'))


def load_catalog_config() -> dict[str, Any]:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding='utf-8'))


def load_history(product_id: str) -> list[dict[str, Any]]:
    history_path = DATA_DIR / 'products' / f'{product_id}.json'
    if not history_path.exists():
        return []
    return json.loads(history_path.read_text(encoding='utf-8'))


def is_lens_category(category_id: str) -> bool:
    return category_id.endswith('-lenses')


def sort_products(products: list[dict[str, Any]], category_id: str) -> list[dict[str, Any]]:
    items = list(products)
    if category_id in BODY_CATEGORIES:
        items.sort(key=lambda item: item.get('release_year') or 0, reverse=True)
    elif is_lens_category(category_id):
        items.sort(key=lambda item: item.get('focal_length_min') or 0)
    return items


def merge_catalog_with_config(live_catalog: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    metric_defaults = {
        'median': None,
        'mean': None,
        'min': None,
        'max': None,
        'q1': None,
        'q3': None,
        'count': 0,
        'count_filtered': 0,
        'samples': [],
        'deals': [],
    }
    live_categories = {category['id']: category for category in live_catalog.get('categories', [])}
    merged_categories = []

    for config_category in config.get('categories', []):
        live_category = live_categories.get(config_category['id'], {})
        live_products = {
            product['id']: product
            for product in live_category.get('products', [])
        }
        merged_products = []

        for config_product in config_category.get('products', []):
            live_product = live_products.get(config_product['id'], {})
            merged_product = dict(metric_defaults)
            merged_product.update(live_product)
            merged_product.update(
                {
                    key: value
                    for key, value in config_product.items()
                    if key not in {'query', 'category_id', 'search_category_id', 'min_price', 'max_price'}
                }
            )
            for field in RARITY_FIELDS:
                if field not in config_product:
                    merged_product.pop(field, None)
            merged_product['samples'] = live_product.get('samples', [])
            merged_product['deals'] = live_product.get('deals', [])
            merged_products.append(merged_product)

        merged_categories.append(
            {
                'id': config_category['id'],
                'name_ko': config_category['name_ko'],
                'name_en': config_category['name_en'],
                'subcategories': config_category.get('subcategories', []),
                'products': merged_products,
            }
        )

    return {
        'updated': live_catalog.get('updated', date.today().isoformat()),
        'exchange_rate': live_catalog.get('exchange_rate'),
        'categories': merged_categories,
    }


def compute_stale_days(updated: str) -> int:
    updated_date = date.fromisoformat(updated)
    return (datetime.now().date() - updated_date).days


def compute_price_change(history: list[dict[str, Any]], days: int) -> dict[str, Any] | None:
    valid = [entry for entry in history if entry.get('median') is not None]
    if len(valid) < 2:
        return None

    latest = valid[-1]
    latest_date = date.fromisoformat(latest['date'])
    cutoff = latest_date - timedelta(days=days)
    baseline = None
    for entry in reversed(valid[:-1]):
        if date.fromisoformat(entry['date']) <= cutoff:
            baseline = entry
            break

    if baseline is None:
        baseline = valid[0]
        if baseline['date'] == latest['date']:
            return None

    baseline_price = float(baseline['median'])
    latest_price = float(latest['median'])
    if baseline_price <= 0:
        return None

    delta_value = latest_price - baseline_price
    delta_pct = (delta_value / baseline_price) * 100
    baseline_date = date.fromisoformat(baseline['date'])
    return {
        'days': days,
        'baseline_date': baseline['date'],
        'baseline_median': baseline_price,
        'latest_date': latest['date'],
        'latest_median': latest_price,
        'delta_value': delta_value,
        'delta_pct': delta_pct,
        'actual_days': (latest_date - baseline_date).days,
    }


def has_catalog_listing_data(product: dict[str, Any]) -> bool:
    return (product.get('count') or 0) > 0


def should_show_home_catalog_product(category_id: str, product: dict[str, Any]) -> bool:
    # Show the full DSLR lineup even before the first scrape so coverage is visible.
    return category_id == 'f-mount-dslr' or has_catalog_listing_data(product)
