"""fetch.main 배선 테스트 (네트워크·파일 쓰기 전면 모킹).

실제 eBay/OpenRouter를 호출하지 않고, 수집 종료 시
계측 기록·환율 시계열 append·max_price 되먹임이 배선되어 있는지 확인한다.
제품별 예외 격리와 실패율 안전장치도 여기서 검증한다.
"""

from __future__ import annotations

import json

import pytest
import requests

from nikon_value import fetch, storage
from nikon_value.metrics import RunMetrics

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


def _search_that_fails(failures: dict[str, BaseException]):
    """지정한 제품에서만 예외를 던지고 나머지는 정상 수집하는 검색 스텁."""
    healthy = _fake_search({})

    def search(token, browse_url, product, expand_when_empty=True, metrics=None):
        error = failures.get(product["id"])
        if error is not None:
            raise error
        return healthy(
            token, browse_url, product,
            expand_when_empty=expand_when_empty, metrics=metrics,
        )
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


# --- 제품별 예외 격리 (D-16) -------------------------------------------


def test_a_non_request_error_in_one_product_does_not_kill_the_whole_run(pipeline, monkeypatch):
    """extract_price의 float() 변환 실패 같은 예외도 제품 단위로 격리된다."""
    calls, data_dir = pipeline
    monkeypatch.setattr(fetch, "PRODUCT_FAILURE_RATE_THRESHOLD", 0.9)
    monkeypatch.setattr(
        fetch, "search_items_for_product",
        _search_that_fails({"nikon-z9": ValueError("could not convert string to float: ''")}),
    )

    fetch.main()

    catalog = json.loads((data_dir / "catalog.json").read_text(encoding="utf-8"))
    products = {p["id"]: p for p in catalog["categories"][0]["products"]}
    # 실패한 제품은 빈 통계 + error 필드로 남는다
    assert products["nikon-z9"]["count"] == 0
    assert products["nikon-z9"]["samples"] == []
    assert "could not convert" in products["nikon-z9"]["error"]
    # 나머지 제품 수집과 결과 저장은 그대로 진행된다
    assert products["nikon-z8"]["count"] == 3
    assert len(calls["exchange"]) == 1
    assert calls["metrics"][0]["products_processed"] == 2
    assert calls["metrics"][0]["products_failed"] == 1
    # 실패한 제품의 시계열이 0건으로 오염되지 않는다
    assert not (data_dir / "products" / "nikon-z9.json").exists()
    assert (data_dir / "products" / "nikon-z8.json").exists()


def test_request_errors_stay_isolated_and_are_counted_as_failures(pipeline, monkeypatch):
    calls, data_dir = pipeline
    monkeypatch.setattr(fetch, "PRODUCT_FAILURE_RATE_THRESHOLD", 0.9)
    monkeypatch.setattr(
        fetch, "search_items_for_product",
        _search_that_fails({"nikon-z8": requests.exceptions.ConnectionError("connection reset")}),
    )

    fetch.main()

    catalog = json.loads((data_dir / "catalog.json").read_text(encoding="utf-8"))
    products = {p["id"]: p for p in catalog["categories"][0]["products"]}
    assert products["nikon-z8"]["error"] == "connection reset"
    assert products["nikon-z9"]["count"] == 3
    assert calls["metrics"][0]["products_failed"] == 1


def test_a_wholesale_failure_exits_non_zero_after_saving_what_it_has(pipeline, monkeypatch):
    """토큰 만료·eBay 전면 장애가 조용한 성공으로 위장되면 안 된다."""
    calls, data_dir = pipeline
    monkeypatch.setattr(
        fetch, "search_items_for_product",
        _search_that_fails({
            "nikon-z9": RuntimeError("token expired"),
            "nikon-z8": RuntimeError("token expired"),
        }),
    )

    with pytest.raises(SystemExit) as excinfo:
        fetch.main()

    assert excinfo.value.code == 1
    # 종료 전에 계측과 부분 결과는 남긴다(원인 추적용). 워크플로는 실패로 끝나므로
    # 이 catalog.json이 커밋되지는 않는다.
    assert calls["metrics"][0]["products_failed"] == 2
    assert (data_dir / "catalog.json").exists()


def test_keyboard_interrupt_is_never_swallowed(pipeline, monkeypatch):
    calls, data_dir = pipeline

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(fetch, "search_items_for_product", interrupt)

    with pytest.raises(KeyboardInterrupt):
        fetch.main()

    assert calls["metrics"] == []
    assert not (data_dir / "catalog.json").exists()


@pytest.mark.parametrize(
    ("processed", "failed", "should_exit"),
    [
        (10, 0, False),
        (10, 2, False),   # 20%는 임계값 이하 — 격리된 실패로 본다
        (10, 3, True),
        (331, 66, False), # 실제 카탈로그 규모에서의 경계
        (331, 67, True),
        (1, 1, True),     # --only 단일 제품 실행이 통째로 실패한 경우
    ],
)
def test_failure_threshold_boundary(processed, failed, should_exit):
    metrics = RunMetrics(products_processed=processed, products_failed=failed)

    if not should_exit:
        fetch.enforce_failure_threshold(metrics)
        return

    with pytest.raises(SystemExit) as excinfo:
        fetch.enforce_failure_threshold(metrics)
    assert excinfo.value.code == 1


# --- 분해된 단위 -------------------------------------------------------


def test_process_product_returns_an_outcome_instead_of_mutating_shared_state(pipeline, monkeypatch):
    """process_product는 되먹임 값을 반환만 하고 설정 파일을 건드리지 않는다."""
    _, data_dir = pipeline
    monkeypatch.setattr(fetch, "search_items_for_product", _fake_search({"nikon-z9": 7000}))
    ctx = fetch.RunContext(
        token="token",
        browse_url="https://browse.example",
        catalog_config=json.loads(json.dumps(CATALOG)),
        today="2026-07-28",
        metrics=RunMetrics(),
    )
    product = ctx.catalog_config["categories"][0]["products"][0]

    outcome = fetch.process_product(ctx, product)

    assert outcome.failed is False
    assert outcome.max_price_update == 7000
    assert outcome.entry["id"] == "nikon-z9"
    assert outcome.entry["count"] == 3
    assert len(outcome.entry["samples"]) == 3
    history = json.loads((data_dir / "products" / "nikon-z9.json").read_text(encoding="utf-8"))
    assert [h["date"] for h in history] == ["2026-07-28"]


def test_apply_llm_filter_runs_only_when_there_is_a_key_and_items(monkeypatch):
    product = CATALOG["categories"][0]["products"][0]
    items = [{"title": "Nikon Z9 body"}, {"title": "Nikon Z9 for parts"}]
    ctx = fetch.RunContext(
        token="token",
        browse_url="https://browse.example",
        catalog_config=CATALOG,
        today="2026-07-28",
        metrics=RunMetrics(),
    )

    # 키가 없으면(=LLM 비활성) 규칙 필터 결과를 그대로 쓴다
    assert fetch.apply_llm_filter(ctx, product, items) is items

    ctx.openrouter_key = "sk-test"
    assert fetch.apply_llm_filter(ctx, product, []) == []

    monkeypatch.setattr(
        fetch, "filter_items_with_llm",
        lambda items, product, key, cache=None, metrics=None: items[:1],
    )
    assert fetch.apply_llm_filter(ctx, product, items) == items[:1]


def test_missing_ebay_credentials_stop_the_run_immediately(monkeypatch):
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        fetch.load_ebay_credentials()

    assert excinfo.value.code == 1


def test_a_sandbox_client_id_switches_to_the_sandbox_endpoints(pipeline, monkeypatch):
    monkeypatch.setenv("EBAY_CLIENT_ID", "SBX-test-id")

    ctx = fetch.build_run_context(fetch.parse_args())

    assert "sandbox" in ctx.browse_url


def test_llm_resources_are_only_loaded_when_a_key_is_present(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "load_openrouter_key", lambda: None)
    assert fetch.load_llm_resources() == (None, None)

    monkeypatch.setattr(fetch, "load_openrouter_key", lambda: "sk-test")
    monkeypatch.setattr(
        fetch.LlmDecisionCache, "load",
        classmethod(lambda cls: fetch.LlmDecisionCache(path=tmp_path / "llm-cache.json")),
    )
    key, cache = fetch.load_llm_resources()
    assert key == "sk-test"
    assert cache is not None


def test_write_outputs_saves_a_dirty_llm_cache(pipeline, tmp_path):
    _, data_dir = pipeline
    cache = fetch.LlmDecisionCache(path=tmp_path / "llm-cache.json")
    cache.record("nikon-z9", "Nikon Z9 body", True)
    ctx = fetch.RunContext(
        token="token",
        browse_url="https://browse.example",
        catalog_config=CATALOG,
        today="2026-07-28",
        metrics=RunMetrics(),
        llm_cache=cache,
    )

    fetch.write_outputs(ctx, {"updated": "2026-07-28", "categories": []}, {})

    assert (tmp_path / "llm-cache.json").exists()
    assert cache.dirty is False
    assert (data_dir / "catalog.json").exists()


# --- 환율 3단계 폴백 ---------------------------------------------------


def test_exchange_rate_prefers_a_fresh_ecb_lookup(monkeypatch):
    monkeypatch.setattr(fetch, "fetch_usd_krw_exchange_rate", lambda: dict(EXCHANGE_RATE))

    assert fetch.resolve_exchange_rate({"rate": 1.0}) == EXCHANGE_RATE


def _ecb_down(monkeypatch):
    def boom():
        raise RuntimeError("ECB unreachable")
    monkeypatch.setattr(fetch, "fetch_usd_krw_exchange_rate", boom)


def test_exchange_rate_keeps_the_existing_catalog_value_when_the_lookup_fails(monkeypatch):
    _ecb_down(monkeypatch)
    monkeypatch.setattr(fetch, "recover_exchange_rate_from_history", lambda: {"rate": 1.0})

    assert fetch.resolve_exchange_rate(dict(EXCHANGE_RATE)) == EXCHANGE_RATE


def test_exchange_rate_recovers_from_history_when_there_is_no_existing_value(monkeypatch):
    _ecb_down(monkeypatch)
    recovered = {"base": "USD", "quote": "KRW", "rate": 1400.0}
    monkeypatch.setattr(fetch, "recover_exchange_rate_from_history", lambda: recovered)

    assert fetch.resolve_exchange_rate(None) == recovered


def test_exchange_rate_gives_up_only_after_both_fallbacks(monkeypatch):
    _ecb_down(monkeypatch)
    monkeypatch.setattr(fetch, "recover_exchange_rate_from_history", lambda: None)

    assert fetch.resolve_exchange_rate(None) is None


def test_reuse_existing_entry_falls_back_to_an_empty_entry(pipeline):
    ctx = fetch.RunContext(
        token="token",
        browse_url="https://browse.example",
        catalog_config=CATALOG,
        today="2026-07-28",
        metrics=RunMetrics(),
        existing_products={"nikon-z9": {"id": "nikon-z9", "count": 7}},
    )
    products = CATALOG["categories"][0]["products"]

    assert fetch.reuse_existing_entry(ctx, products[0]) == {"id": "nikon-z9", "count": 7}
    empty = fetch.reuse_existing_entry(ctx, products[1])
    assert empty["id"] == "nikon-z8"
    assert empty["count"] == 0
    assert empty["samples"] == []
    assert "error" not in empty
