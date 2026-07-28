"""수집 계측(run metrics) 수집기와 ebay/llm 카운터 연동 테스트."""

from __future__ import annotations

import json

import pytest

from nikon_value import ebay, llm, metrics
from nikon_value.metrics import RunMetrics, append_run_metrics, load_run_metrics, reset_metrics


@pytest.fixture(autouse=True)
def _isolated_active_metrics():
    """전역 활성 수집기가 테스트 간에 새지 않도록 초기화한다."""
    reset_metrics()
    yield
    reset_metrics()


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected status {self.status_code}")

    def json(self):
        return self.payload


def test_run_metrics_accumulates_counters_and_serializes_without_internals():
    m = RunMetrics()
    m.record_product()
    m.record_ebay_search(2)
    m.record_http_request(3)
    m.record_rate_limited()
    m.record_llm_call()
    m.record_llm_cache_hits(5)
    m.record_llm_cache_misses(2)
    m.record_max_price_expansion()

    payload = m.finish().to_dict()

    assert payload["products_processed"] == 1
    assert payload["ebay_search_calls"] == 2
    assert payload["ebay_http_requests"] == 3
    assert payload["ebay_rate_limited"] == 1
    assert payload["llm_calls"] == 1
    assert payload["llm_cache_hits"] == 5
    assert payload["llm_cache_misses"] == 2
    assert payload["max_price_expansions"] == 1
    assert payload["duration_seconds"] >= 0
    assert "_monotonic_start" not in payload
    assert payload["started_at"].endswith("+00:00")


def test_summary_line_mentions_every_counter():
    m = RunMetrics(products_processed=3, ebay_search_calls=5, ebay_http_requests=7,
                   ebay_rate_limited=1, llm_calls=2, llm_cache_hits=9, llm_cache_misses=4,
                   max_price_expansions=2)
    line = m.finish().summary_line()

    assert "3 products" in line
    assert "5 eBay searches" in line
    assert "7 HTTP requests" in line
    assert "1 rate limited" in line
    assert "2 LLM calls" in line
    assert "9 cache hits" in line
    assert "4 misses" in line
    assert "2 max-price expansions" in line


def test_append_run_metrics_rolls_to_the_configured_limit(tmp_path):
    path = tmp_path / "run-metrics.json"

    for i in range(5):
        append_run_metrics(RunMetrics(products_processed=i), path=path, limit=3)

    runs = load_run_metrics(path)
    assert [r["products_processed"] for r in runs] == [2, 3, 4]
    assert json.loads(path.read_text(encoding="utf-8"))["runs"] == runs


def test_load_run_metrics_tolerates_a_corrupt_file(tmp_path):
    path = tmp_path / "run-metrics.json"
    path.write_text("{not json", encoding="utf-8")

    assert load_run_metrics(path) == []

    append_run_metrics(RunMetrics(), path=path, limit=10)
    assert len(load_run_metrics(path)) == 1


def test_reset_metrics_swaps_the_active_collector():
    first = metrics.get_metrics()
    first.record_product()

    second = reset_metrics()

    assert second is metrics.get_metrics()
    assert second is not first
    assert second.products_processed == 0


def test_search_items_counts_http_requests_and_rate_limits(monkeypatch):
    m = RunMetrics()
    responses = [
        _FakeResponse({}, status_code=429),
        _FakeResponse({"itemSummaries": [{"title": "a"}], "total": 1}),
    ]
    monkeypatch.setattr(ebay.requests, "get", lambda *a, **kw: responses.pop(0))
    monkeypatch.setattr(ebay.time, "sleep", lambda *_: None)

    items = ebay.search_items("token", "browse", "q", "3323", 10, 200, metrics=m)

    assert len(items) == 1
    assert m.ebay_http_requests == 2  # 429 재시도 포함
    assert m.ebay_rate_limited == 1
    assert m.ebay_search_calls == 0  # 검색 호출 수는 search_items_for_product가 센다


def test_search_items_for_product_counts_searches_and_expansions(monkeypatch):
    m = RunMetrics()
    product = {
        "id": "nikon-fg",
        "query": "Nikon FG body",
        "category_id": "3323",
        "min_price": 20,
        "max_price": 200,
    }

    def fake_search_items(token, browse_url, query, category_id, min_price, max_price):
        if max_price <= 200:
            return []
        return [{"price": {"value": "215.00"}}]

    monkeypatch.setattr(ebay, "search_items", fake_search_items)
    monkeypatch.setattr(ebay, "filter_items_with_rules", lambda items, product: items)

    ebay.search_items_for_product("token", "browse", product, metrics=m)

    assert m.ebay_search_calls == 2  # 기본 1 + 확장 1
    assert m.max_price_expansions == 1


def test_search_items_for_product_falls_back_to_the_active_collector(monkeypatch):
    product = {
        "id": "nikon-fg",
        "query": "Nikon FG body",
        "category_id": "3323",
        "min_price": 20,
        "max_price": 200,
    }
    monkeypatch.setattr(ebay, "search_items", lambda *a: [{"price": {"value": "50.00"}}])
    monkeypatch.setattr(ebay, "filter_items_with_rules", lambda items, product: items)

    ebay.search_items_for_product("token", "browse", product)

    assert metrics.get_metrics().ebay_search_calls == 1


def test_filter_items_with_llm_counts_calls_and_cache_activity(monkeypatch):
    from nikon_value.llm_cache import LlmDecisionCache

    m = RunMetrics()
    cache = LlmDecisionCache()
    items = [{"title": f"Nikon FG body #{i}"} for i in range(3)]
    product = {"id": "nikon-fg", "name_en": "Nikon FG", "query": "Nikon FG body"}
    monkeypatch.setattr(
        llm.requests, "post",
        lambda *a, **kw: _FakeResponse(
            {"choices": [{"message": {"content": '{"indices": [0, 1, 2]}'}}]}
        ),
    )

    llm.filter_items_with_llm(items, product, "key", cache=cache, metrics=m)
    assert (m.llm_calls, m.llm_cache_hits, m.llm_cache_misses) == (1, 0, 3)

    # 두 번째 실행은 전부 캐시 히트라 LLM을 호출하지 않는다.
    llm.filter_items_with_llm(items, product, "key", cache=cache, metrics=m)
    assert (m.llm_calls, m.llm_cache_hits, m.llm_cache_misses) == (1, 3, 3)
