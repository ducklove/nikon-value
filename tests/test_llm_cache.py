"""LLM 판정 캐시와 폴백 규율 보존 테스트.

이 프로젝트의 핵심 자산은 "자동 필터는 틀릴 수 있으므로 데이터를 지우지
않는다"는 폴백 규율이다. 캐시 도입 후에도 그 의미가 유지되는지 확인한다.
"""

from __future__ import annotations

import json

import pytest

from nikon_value import llm
from nikon_value.llm_cache import LlmDecisionCache, title_key
from nikon_value.metrics import RunMetrics

PRODUCT = {"id": "nikon-fg", "name_en": "Nikon FG", "query": "Nikon FG body"}


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _items(count: int) -> list[dict]:
    return [{"title": f"Nikon FG body #{i}"} for i in range(count)]


def _stub_llm(monkeypatch, indices, recorder: list | None = None):
    """OpenRouter 응답을 고정한다. recorder에는 전송된 프롬프트가 쌓인다."""

    def fake_post(url, headers=None, json=None, timeout=None):
        if recorder is not None:
            recorder.append(json["messages"][1]["content"])
        content = f'{{"indices": {list(indices)}}}'
        return _FakeResponse({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(llm.requests, "post", fake_post)


def _forbid_llm(monkeypatch):
    def fake_post(*args, **kwargs):
        raise AssertionError("LLM must not be called when every title is cached")

    monkeypatch.setattr(llm.requests, "post", fake_post)


# --- 캐시 키 ---------------------------------------------------------
def test_title_key_ignores_case_and_whitespace_noise():
    assert title_key("Nikon  FG   body") == title_key("nikon fg body")
    assert title_key("Nikon FG body") != title_key("Nikon FE body")


# --- 히트/미스/부분히트 ----------------------------------------------
def test_cold_cache_sends_every_title_and_records_decisions(monkeypatch):
    cache = LlmDecisionCache()
    items = _items(3)
    _stub_llm(monkeypatch, [0, 2])

    filtered = llm.filter_items_with_llm(items, PRODUCT, "key", cache=cache)

    assert filtered == [items[0], items[2]]
    assert cache.entry_count() == 3
    assert cache.lookup("nikon-fg", items[0]["title"]) is True
    assert cache.lookup("nikon-fg", items[1]["title"]) is False


def test_full_cache_hit_skips_the_llm_entirely(monkeypatch):
    cache = LlmDecisionCache()
    items = _items(3)
    cache.record("nikon-fg", items[0]["title"], True)
    cache.record("nikon-fg", items[1]["title"], False)
    cache.record("nikon-fg", items[2]["title"], True)
    _forbid_llm(monkeypatch)

    filtered = llm.filter_items_with_llm(items, PRODUCT, "key", cache=cache)

    assert filtered == [items[0], items[2]]


def test_partial_hit_sends_only_uncached_titles(monkeypatch):
    cache = LlmDecisionCache()
    items = _items(4)
    cache.record("nikon-fg", items[0]["title"], True)
    cache.record("nikon-fg", items[1]["title"], False)
    prompts: list[str] = []
    # 미캐시 목록(items[2], items[3]) 기준 인덱스 1 = items[3]
    _stub_llm(monkeypatch, [1], recorder=prompts)

    filtered = llm.filter_items_with_llm(items, PRODUCT, "key", cache=cache)

    assert filtered == [items[0], items[3]]
    assert items[2]["title"] in prompts[0]
    assert items[3]["title"] in prompts[0]
    assert items[0]["title"] not in prompts[0]  # 캐시된 타이틀은 재전송하지 않는다
    assert cache.lookup("nikon-fg", items[2]["title"]) is False
    assert cache.lookup("nikon-fg", items[3]["title"]) is True


def test_cache_is_scoped_per_product(monkeypatch):
    cache = LlmDecisionCache()
    items = _items(2)
    cache.record("nikon-fe", items[0]["title"], True)
    prompts: list[str] = []
    _stub_llm(monkeypatch, [0, 1], recorder=prompts)

    llm.filter_items_with_llm(items, PRODUCT, "key", cache=cache)

    # 다른 제품의 판정은 재사용되지 않는다.
    assert items[0]["title"] in prompts[0]


# --- 폴백 규율 -------------------------------------------------------
def test_llm_filtering_everything_keeps_the_heuristic_set_and_does_not_cache(monkeypatch):
    cache = LlmDecisionCache()
    items = _items(8)
    _stub_llm(monkeypatch, [])

    filtered = llm.filter_items_with_llm(items, PRODUCT, "key", cache=cache)

    assert filtered == items
    assert cache.entry_count() == 0  # 신뢰할 수 없는 판정은 캐시에 남기지 않는다


def test_small_set_filtered_to_nothing_is_accepted_without_caching(monkeypatch):
    cache = LlmDecisionCache()
    items = _items(3)
    _stub_llm(monkeypatch, [])

    assert llm.filter_items_with_llm(items, PRODUCT, "key", cache=cache) == items
    assert cache.entry_count() == 0


def test_llm_exception_keeps_the_heuristic_set_and_does_not_cache(monkeypatch):
    cache = LlmDecisionCache()
    items = _items(6)

    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(llm.requests, "post", boom)

    assert llm.filter_items_with_llm(items, PRODUCT, "key", cache=cache) == items
    assert cache.entry_count() == 0


def test_all_cache_hits_rejecting_everything_still_keeps_the_heuristic_set(monkeypatch):
    """캐시 히트만으로 전부 탈락하는 경우에도 같은 폴백이 적용되어야 한다."""
    cache = LlmDecisionCache()
    items = _items(8)
    for item in items:
        cache.record("nikon-fg", item["title"], False)
    _forbid_llm(monkeypatch)

    assert llm.filter_items_with_llm(items, PRODUCT, "key", cache=cache) == items


def test_partial_hit_where_combined_result_is_empty_keeps_everything(monkeypatch):
    cache = LlmDecisionCache()
    items = _items(6)
    for item in items[:3]:
        cache.record("nikon-fg", item["title"], False)
    _stub_llm(monkeypatch, [])

    filtered = llm.filter_items_with_llm(items, PRODUCT, "key", cache=cache)

    assert filtered == items
    # 폴백이 걸린 실행에서는 새 판정을 남기지 않는다(기존 3건만 유지).
    assert cache.entry_count() == 3


def test_cached_rejections_never_shrink_a_result_to_zero_across_reruns(monkeypatch):
    """1회차 폴백 → 2회차에도 데이터가 사라지지 않는다."""
    cache = LlmDecisionCache()
    items = _items(7)
    _stub_llm(monkeypatch, [])

    first = llm.filter_items_with_llm(items, PRODUCT, "key", cache=cache)
    second = llm.filter_items_with_llm(items, PRODUCT, "key", cache=cache)

    assert first == items
    assert second == items


def test_filter_without_a_cache_behaves_like_before(monkeypatch):
    items = _items(4)
    _stub_llm(monkeypatch, [1, 3])

    assert llm.filter_items_with_llm(items, PRODUCT, "key") == [items[1], items[3]]


def test_empty_item_list_short_circuits(monkeypatch):
    _forbid_llm(monkeypatch)
    m = RunMetrics()

    assert llm.filter_items_with_llm([], PRODUCT, "key", cache=LlmDecisionCache(), metrics=m) == []
    assert m.llm_calls == 0


def test_out_of_range_indices_are_ignored(monkeypatch):
    cache = LlmDecisionCache()
    items = _items(3)
    _stub_llm(monkeypatch, [0, 99, -1])

    assert llm.filter_items_with_llm(items, PRODUCT, "key", cache=cache) == [items[0]]


# --- 지속성/정리 -----------------------------------------------------
def test_cache_round_trips_through_a_file(tmp_path):
    path = tmp_path / "llm-cache.json"
    cache = LlmDecisionCache(path=path)
    cache.record("nikon-fg", "Nikon FG body", True)
    cache.record("nikon-fg", "Nikon FG hood", False)
    cache.save()

    reloaded = LlmDecisionCache.load(path)

    assert reloaded.lookup("nikon-fg", "Nikon FG body") is True
    assert reloaded.lookup("nikon-fg", "Nikon FG hood") is False
    assert reloaded.lookup("nikon-fg", "unseen title") is None
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_load_tolerates_a_missing_or_corrupt_file(tmp_path):
    missing = tmp_path / "nope.json"
    assert LlmDecisionCache.load(missing).entry_count() == 0

    broken = tmp_path / "broken.json"
    broken.write_text("{oops", encoding="utf-8")
    assert LlmDecisionCache.load(broken).entry_count() == 0


def test_prune_keeps_the_most_recently_used_entries_per_product():
    cache = LlmDecisionCache(max_entries_per_product=3)
    for i in range(10):
        cache.record("nikon-fg", f"title {i}", True, now=1000 + i)

    removed = cache.prune()

    assert removed == 7
    assert cache.entry_count() == 3
    for i in range(7, 10):
        assert cache.lookup("nikon-fg", f"title {i}") is True
    for i in range(0, 7):
        assert cache.lookup("nikon-fg", f"title {i}") is None


def test_lookup_refreshes_the_recency_timestamp_so_hot_entries_survive_pruning():
    cache = LlmDecisionCache(max_entries_per_product=2)
    cache.record("nikon-fg", "old but hot", True, now=1)
    cache.record("nikon-fg", "middle", True, now=2)
    cache.record("nikon-fg", "newest", True, now=3)

    cache.lookup("nikon-fg", "old but hot", now=99)
    cache.prune()

    assert cache.lookup("nikon-fg", "old but hot") is True
    assert cache.lookup("nikon-fg", "middle") is None


def test_save_prunes_before_writing(tmp_path):
    path = tmp_path / "llm-cache.json"
    cache = LlmDecisionCache(path=path, max_entries_per_product=2)
    for i in range(5):
        cache.record("nikon-fg", f"title {i}", True, now=i)
    cache.save()

    assert LlmDecisionCache.load(path).entry_count() == 2
    assert cache.dirty is False


@pytest.mark.parametrize("keep", [True, False])
def test_record_marks_the_cache_dirty(keep):
    cache = LlmDecisionCache()
    assert cache.dirty is False
    cache.record("nikon-fg", "a title", keep)
    assert cache.dirty is True
