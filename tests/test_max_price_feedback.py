"""적응형 상한 되먹임(products.yaml max_price 갱신)과 안전장치 테스트."""

from __future__ import annotations

import yaml

from nikon_value import storage
from nikon_value.paths import CONFIG_PATH
from nikon_value.storage import update_catalog_max_prices

SAMPLE_YAML = """\
# 니콘 중고 시세 추적 대상 카탈로그
categories:
- id: z-mount-bodies
  name_ko: Z마운트 바디
  name_en: Z-Mount Bodies
  subcategories:
  # 정렬 순서는 사이트 좌측 메뉴에 그대로 쓰인다
  - id: flagship
    name_ko: 플래그십
    name_en: Flagship
    sort_order: 1
  products:
  - id: nikon-z9
    name_ko: 니콘 Z9
    name_en: Nikon Z9
    subcategory: flagship
    query: Nikon Z9 body
    category_id: '31388'
    min_price: 2000
    max_price: 6000
  # 아래 제품은 상한이 자주 걸린다
  - id: nikon-z8
    name_ko: 니콘 Z8
    name_en: Nikon Z8
    query: Nikon Z8 body
    category_id: '31388'
    min_price: 2000
    max_price: 4500
    exclude_title_patterns:
    - z8 hood
  - id: nikon-zf
    name_ko: 니콘 Zf
    name_en: Nikon Zf
    query: Nikon Zf body
    category_id: '31388'
    min_price: 1200
    max_price: 2500
"""


def _write_sample(tmp_path):
    path = tmp_path / "products.yaml"
    path.write_text(SAMPLE_YAML, encoding="utf-8")
    return path


def _products(path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {p["id"]: p for p in storage.iter_config_products(doc)}


def test_feedback_updates_only_the_targeted_max_price_lines(tmp_path):
    path = _write_sample(tmp_path)

    updated = update_catalog_max_prices({"nikon-z8": 6800}, path=path)

    assert updated == ["nikon-z8"]
    products = _products(path)
    assert products["nikon-z8"]["max_price"] == 6800
    assert products["nikon-z8"]["min_price"] == 2000  # min_price는 건드리지 않는다
    assert products["nikon-z9"]["max_price"] == 6000  # 다른 제품도 그대로


def test_feedback_preserves_comments_order_and_structure(tmp_path):
    path = _write_sample(tmp_path)
    before_lines = path.read_text(encoding="utf-8").splitlines()

    update_catalog_max_prices({"nikon-z9": 9000, "nikon-z8": 6800}, path=path)

    after_lines = path.read_text(encoding="utf-8").splitlines()
    assert len(after_lines) == len(before_lines)
    changed = [
        (b, a) for b, a in zip(before_lines, after_lines, strict=True) if b != a
    ]
    assert changed == [
        ("    max_price: 6000", "    max_price: 9000"),
        ("    max_price: 4500", "    max_price: 6800"),
    ]
    # 주석과 나머지 구조가 모두 살아 있다
    text = path.read_text(encoding="utf-8")
    assert "# 니콘 중고 시세 추적 대상 카탈로그" in text
    assert "# 정렬 순서는 사이트 좌측 메뉴에 그대로 쓰인다" in text
    assert "# 아래 제품은 상한이 자주 걸린다" in text
    assert "- z8 hood" in text


def test_feedback_writes_integral_values_without_a_decimal_point(tmp_path):
    path = _write_sample(tmp_path)

    update_catalog_max_prices({"nikon-z9": 9000.0}, path=path)

    assert "max_price: 9000\n" in path.read_text(encoding="utf-8")
    assert _products(path)["nikon-z9"]["max_price"] == 9000


def test_empty_update_set_is_a_no_op(tmp_path):
    path = _write_sample(tmp_path)

    assert update_catalog_max_prices({}, path=path) == []
    assert path.read_text(encoding="utf-8") == SAMPLE_YAML


def test_unknown_product_id_changes_nothing(tmp_path):
    path = _write_sample(tmp_path)

    assert update_catalog_max_prices({"nikon-does-not-exist": 100}, path=path) == []
    assert path.read_text(encoding="utf-8") == SAMPLE_YAML


def test_subcategory_ids_are_never_mistaken_for_products(tmp_path):
    path = _write_sample(tmp_path)

    assert update_catalog_max_prices({"flagship": 1}, path=path) == []
    assert path.read_text(encoding="utf-8") == SAMPLE_YAML


def test_guard_aborts_when_a_rewrite_would_add_a_field(tmp_path, monkeypatch):
    path = _write_sample(tmp_path)
    monkeypatch.setattr(storage, "_format_max_price", lambda v: "9000\n    injected: 1")

    assert update_catalog_max_prices({"nikon-z9": 9000}, path=path) == []
    assert path.read_text(encoding="utf-8") == SAMPLE_YAML  # 아무것도 쓰지 않는다


def test_guard_aborts_when_the_written_value_does_not_parse_back(tmp_path, monkeypatch):
    path = _write_sample(tmp_path)
    monkeypatch.setattr(storage, "_format_max_price", lambda v: "null")

    assert update_catalog_max_prices({"nikon-z9": 9000}, path=path) == []
    assert path.read_text(encoding="utf-8") == SAMPLE_YAML


def test_guard_aborts_when_a_rewrite_would_add_a_product(tmp_path, monkeypatch):
    path = _write_sample(tmp_path)
    injected = (
        "2600\n"
        "  - id: nikon-injected\n"
        "    name_ko: 침입\n"
        "    name_en: Injected\n"
        "    min_price: 1\n"
        "    max_price: 2"
    )
    monkeypatch.setattr(storage, "_format_max_price", lambda v: injected)

    assert update_catalog_max_prices({"nikon-zf": 2600}, path=path) == []
    assert path.read_text(encoding="utf-8") == SAMPLE_YAML


def test_guard_aborts_when_an_untargeted_product_max_price_would_move(tmp_path, monkeypatch):
    """제품 수·ID·다른 필드가 모두 같아도, 엉뚱한 제품의 상한이 움직이면 중단한다."""
    path = _write_sample(tmp_path)
    real_load = yaml.safe_load
    calls = {"n": 0}

    def fake_load(text):
        doc = real_load(text)
        calls["n"] += 1
        if calls["n"] == 2:  # 저장 직전 'after' 파싱 결과만 조작한다
            for product in storage.iter_config_products(doc):
                if product["id"] == "nikon-z8":
                    product["max_price"] = 1
        return doc

    monkeypatch.setattr(storage.yaml, "safe_load", fake_load)

    assert update_catalog_max_prices({"nikon-z9": 9000}, path=path) == []
    assert calls["n"] == 2  # 안전장치가 after 파싱 이후 단계에서 걸렸다
    assert path.read_text(encoding="utf-8") == SAMPLE_YAML


def test_guard_aborts_when_a_rewrite_would_not_parse(tmp_path, monkeypatch):
    path = _write_sample(tmp_path)
    monkeypatch.setattr(storage, "_format_max_price", lambda v: "9000\n  - broken: [")

    assert update_catalog_max_prices({"nikon-z9": 9000}, path=path) == []
    assert path.read_text(encoding="utf-8") == SAMPLE_YAML


def test_real_config_survives_a_round_trip_through_the_feedback_writer(tmp_path):
    """실제 config/products.yaml 사본으로 되먹임해도 구조가 보존된다."""
    path = tmp_path / "products.yaml"
    path.write_text(CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    before = yaml.safe_load(path.read_text(encoding="utf-8"))
    before_products = list(storage.iter_config_products(before))
    target = before_products[0]["id"]

    updated = update_catalog_max_prices({target: 99999}, path=path)

    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    after_products = list(storage.iter_config_products(after))
    assert updated == [target]
    assert len(after_products) == len(before_products)
    assert [p["id"] for p in after_products] == [p["id"] for p in before_products]
    assert after_products[0]["max_price"] == 99999
    assert storage._config_fingerprint(before) == storage._config_fingerprint(after)
