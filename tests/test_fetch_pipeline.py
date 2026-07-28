"""fetch.main 배선 테스트 (네트워크·파일 쓰기 전면 모킹).

실제 eBay/OpenRouter를 호출하지 않고, 수집 종료 시
계측 기록·환율 시계열 append·max_price 되먹임이 배선되어 있는지 확인한다.
"""

from __future__ import annotations

import json

import pytest

from nikon_value import fetch, storage

CATALOG = {
    "categories": [
        {
            "id": "z-mount-bodies",
            "name_ko": "Z마운트 바디",
            "name_en": "Z-Mount Bodies",
            "products": [
                {
                    "id": "nikon-z9",
                    "name_ko": "니콘 Z9",
                    "name_en": "Nikon Z9",
                    "query": "Nikon Z9 body",
                    "category_id": "31388",
                    "min_price": 2000,
                    "max_price": 6000,
                },
                {
                    "id": "nikon-z8",
                    "name_ko": "니콘 Z8",
                    "name_en": "Nikon Z8",
                    "query": "Nikon Z8 body",
                    "category_id": "31388",
                    "min_price": 2000,
                    "max_price": 4500,
                },
            ],
        }
    ]
}

EXCHANGE_RATE = {
    "base": "USD",
    "quote": "KRW",
    "rate": 1459.4528,
    "reference_date": "2026-07-28",
    "source": "ECB reference rates",
}


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """main()을 tmp_path 안에서 안전하게 실행할 수 있도록 배선한다."""
    data_dir = tmp_path / "data"
    (data_dir / "products").mkdir(parents=True)

    monkeypatch.setattr(fetch, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(fetch.time, "sleep", lambda *_: None)
    monkeypatch.setattr(fetch, "load_env_file", lambda path: None)
    monkeypatch.setattr(fetch, "get_access_token", lambda *a: "token")
    monkeypatch.setattr(fetch, "load_catalog", lambda: json.loads(json.dumps(CATALOG)))
    monkeypatch.setattr(fetch, "load_openrouter_key", lambda: None)
    monkeypatch.setattr(fetch, "fetch_usd_krw_exchange_rate", lambda: dict(EXCHANGE_RATE))
    monkeypatch.setenv("EBAY_CLIENT_ID", "test-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr("sys.argv", ["fetch_prices.py"])

    calls: dict[str, list] = {"exchange": [], "metrics": [], "feedback": []}
    monkeypatch.setattr(
        fetch, "append_exchange_rate",
        lambda rate, date: calls["exchange"].append((date, rate)) or True,
    )
    monkeypatch.setattr(
        fetch, "append_run_metrics", lambda m: calls["metrics"].append(m.to_dict())
    )
    # 실제 config/products.yaml을 건드리지 않는다.
    monkeypatch.setattr(
        fetch, "update_catalog_max_prices",
        lambda updates: calls["feedback"].append(dict(updates)) or sorted(updates),
    )
    return calls, data_dir


def _fake_search(expanded: dict[str, float]):
    def search(token, browse_url, product, expand_when_empty=True, metrics=None):
        if metrics is not None:
            metrics.record_ebay_search()
        max_price = expanded.get(product["id"], product["max_price"])
        items = [
            {"title": f"{product['name_en']} #{i}", "price": {"value": f"{2500 + i}.00"}}
            for i in range(3)
        ]
        return items, max_price
    return search


def test_main_records_metrics_and_appends_the_exchange_rate(pipeline, monkeypatch):
    calls, data_dir = pipeline
    monkeypatch.setattr(fetch, "search_items_for_product", _fake_search({}))

    fetch.main()

    assert len(calls["exchange"]) == 1
    date, rate = calls["exchange"][0]
    assert rate == EXCHANGE_RATE
    assert len(calls["metrics"]) == 1
    assert calls["metrics"][0]["products_processed"] == 2
    assert calls["metrics"][0]["ebay_search_calls"] == 2
    assert calls["metrics"][0]["duration_seconds"] >= 0
    # 제품 히스토리는 계속 기록되고, daily 스냅샷은 더 이상 만들지 않는다.
    assert (data_dir / "products" / "nikon-z9.json").exists()
    assert not (data_dir / "daily").exists()
    assert json.loads((data_dir / "catalog.json").read_text(encoding="utf-8"))["exchange_rate"] == rate


def test_main_feeds_expanded_max_prices_back_into_the_config(pipeline, monkeypatch):
    calls, _ = pipeline
    monkeypatch.setattr(fetch, "search_items_for_product", _fake_search({"nikon-z8": 6800}))

    fetch.main()

    assert calls["feedback"] == [{"nikon-z8": 6800}]


def test_main_skips_feedback_when_no_expansion_happened(pipeline, monkeypatch):
    calls, _ = pipeline
    monkeypatch.setattr(fetch, "search_items_for_product", _fake_search({}))

    fetch.main()

    assert calls["feedback"] == []


def test_main_skips_empty_expansion_for_products_with_a_zero_result_streak(pipeline, monkeypatch):
    _, data_dir = pipeline
    history = [
        {"date": f"2026-07-{20 + i}", "count": 0}
        for i in range(storage.ZERO_RESULT_STREAK_THRESHOLD)
    ]
    (data_dir / "products" / "nikon-z8.json").write_text(json.dumps(history), encoding="utf-8")
    seen: dict[str, bool] = {}

    def search(token, browse_url, product, expand_when_empty=True, metrics=None):
        seen[product["id"]] = expand_when_empty
        return [], product["max_price"]

    monkeypatch.setattr(fetch, "search_items_for_product", search)

    fetch.main()

    assert seen == {"nikon-z9": True, "nikon-z8": False}


def test_main_resumes_normal_expansion_once_listings_reappear(pipeline, monkeypatch):
    _, data_dir = pipeline
    history = [{"date": "2026-07-20", "count": 0}] * 5 + [{"date": "2026-07-26", "count": 4}]
    (data_dir / "products" / "nikon-z8.json").write_text(json.dumps(history), encoding="utf-8")
    seen: dict[str, bool] = {}

    def search(token, browse_url, product, expand_when_empty=True, metrics=None):
        seen[product["id"]] = expand_when_empty
        return [], product["max_price"]

    monkeypatch.setattr(fetch, "search_items_for_product", search)

    fetch.main()

    assert seen["nikon-z8"] is True


def test_main_only_flag_reuses_existing_entries_for_skipped_products(pipeline, monkeypatch):
    calls, data_dir = pipeline
    monkeypatch.setattr(fetch, "search_items_for_product", _fake_search({}))
    monkeypatch.setattr("sys.argv", ["fetch_prices.py", "--only", "nikon-z8"])
    # 이전 실행 결과가 catalog.json에 남아 있는 상태
    fetch.main()
    previous = json.loads((data_dir / "catalog.json").read_text(encoding="utf-8"))

    fetch.main()

    catalog = json.loads((data_dir / "catalog.json").read_text(encoding="utf-8"))
    products = {p["id"]: p for p in catalog["categories"][0]["products"]}
    assert set(products) == {"nikon-z9", "nikon-z8"}
    # --only 대상이 아닌 제품은 기존 catalog 항목이 그대로 유지된다.
    previous_products = {p["id"]: p for p in previous["categories"][0]["products"]}
    assert products["nikon-z9"] == previous_products["nikon-z9"]
    assert products["nikon-z8"]["count"] == 3
    assert calls["metrics"][-1]["products_processed"] == 1
