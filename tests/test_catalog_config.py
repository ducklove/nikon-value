"""config/products.yaml 스키마 검증.

admin UI나 수동 편집으로 들어온 카탈로그 오류(필수 필드 누락, ID 중복,
가격 범위 역전, 끊어진 서브카테고리 참조)를 3시간 수집 사이클이 아니라
CI에서 즉시 잡는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "products.yaml"


@pytest.fixture(scope="module")
def config():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _all_products(config):
    for category in config["categories"]:
        for product in category["products"]:
            yield category, product


def test_category_ids_unique(config):
    ids = [c["id"] for c in config["categories"]]
    assert len(ids) == len(set(ids))


def test_product_ids_globally_unique(config):
    ids = [p["id"] for _, p in _all_products(config)]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    assert duplicates == []


def test_categories_have_required_fields(config):
    for category in config["categories"]:
        for field in ("id", "name_ko", "name_en"):
            assert isinstance(category.get(field), str) and category[field], (
                f"category {category.get('id')}: {field}"
            )
        assert isinstance(category.get("products"), list)


def test_subcategories_are_wellformed(config):
    for category in config["categories"]:
        subcategories = category.get("subcategories", [])
        sub_ids = [s["id"] for s in subcategories]
        assert len(sub_ids) == len(set(sub_ids)), f"category {category['id']}: duplicate subcategory ids"
        for sub in subcategories:
            for field in ("id", "name_ko", "name_en"):
                assert isinstance(sub.get(field), str) and sub[field], (
                    f"subcategory {sub.get('id')} in {category['id']}: {field}"
                )
            assert isinstance(sub.get("sort_order"), int), (
                f"subcategory {sub.get('id')} in {category['id']}: sort_order"
            )


def test_products_have_required_fields(config):
    for category, product in _all_products(config):
        label = f"{category['id']}/{product.get('id')}"
        for field in ("id", "name_ko", "name_en", "query", "subcategory"):
            assert isinstance(product.get(field), str) and product[field], f"{label}: {field}"
        assert isinstance(product.get("category_id"), str) and product["category_id"].isdigit(), (
            f"{label}: category_id must be an eBay numeric category string"
        )


def test_product_subcategory_references_exist(config):
    for category, product in _all_products(config):
        sub_ids = {s["id"] for s in category.get("subcategories", [])}
        assert product["subcategory"] in sub_ids, (
            f"{category['id']}/{product['id']}: unknown subcategory {product['subcategory']!r}"
        )


def test_price_ranges_are_sane(config):
    for category, product in _all_products(config):
        label = f"{category['id']}/{product['id']}"
        min_price = product.get("min_price")
        max_price = product.get("max_price")
        assert isinstance(min_price, (int, float)), f"{label}: min_price"
        assert isinstance(max_price, (int, float)), f"{label}: max_price"
        assert 0 < min_price < max_price, f"{label}: price range {min_price}..{max_price}"


def test_optional_fields_have_expected_shapes(config):
    for category, product in _all_products(config):
        label = f"{category['id']}/{product['id']}"
        if "release_year" in product:
            assert isinstance(product["release_year"], int), f"{label}: release_year"
            assert 1900 <= product["release_year"] <= 2100, f"{label}: release_year"
        if "focal_length_min" in product:
            assert isinstance(product["focal_length_min"], (int, float)), f"{label}: focal_length_min"
            assert product["focal_length_min"] > 0, f"{label}: focal_length_min"
        if "search_category_id" in product:
            sid = product["search_category_id"]
            # null은 의도된 값: 키가 존재하면 fetch가 .get()으로 None을 받아
            # eBay 카테고리 필터 없이 검색한다 (액세서리류에서 사용).
            assert sid is None or (isinstance(sid, str) and sid.isdigit()), (
                f"{label}: search_category_id"
            )
        if "product_type" in product:
            assert product["product_type"] == "accessory", f"{label}: product_type"
        if "exclude_title_patterns" in product:
            patterns = product["exclude_title_patterns"]
            assert isinstance(patterns, list) and patterns, f"{label}: exclude_title_patterns"
            assert all(isinstance(p, str) and p for p in patterns), f"{label}: exclude_title_patterns"


def test_rarity_fields_are_consistent(config):
    rarity_fields = ("rarity_tier", "rarity_sort", "rarity_price_hint", "rarity_note")
    for category, product in _all_products(config):
        label = f"{category['id']}/{product['id']}"
        has_any_rarity = any(field in product for field in rarity_fields)
        if product.get("is_rare"):
            assert "rarity_tier" in product, f"{label}: is_rare without rarity_tier"
        if has_any_rarity:
            assert product.get("is_rare") is True, f"{label}: rarity fields without is_rare"
