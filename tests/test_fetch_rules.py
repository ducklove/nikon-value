import json

from nikon_value import ebay, storage
from scripts.fetch_prices import (
    ZERO_RESULT_STREAK_THRESHOLD,
    count_trailing_zero_results,
    extract_openrouter_indices,
    extract_openrouter_message_text,
    is_obvious_non_match,
    matches_product_exclude_patterns,
    normalize_title,
    round_price_bound,
    search_items_for_product,
    should_expand_max_price,
    strip_json_code_fence,
    zero_result_streak,
)


def test_matches_product_exclude_patterns_uses_normalized_title_fragments():
    product = {"exclude_title_patterns": ["ai-s", "200mm"]}
    normalized_title = normalize_title("NIKON Ai-S 200mm F3.5 AF ED IF F3AF")

    assert matches_product_exclude_patterns(normalized_title, product)


def test_is_obvious_non_match_excludes_f3af_compatibility_lens_listing():
    product = {
        "id": "nikon-f3af",
        "category_id": "3323",
        "exclude_title_patterns": ["ai-s", "80mm", "200mm"],
    }

    assert is_obvious_non_match("NIKON Ai-S 200mm F3.5 AF ED IF F3AF #C1 #101721", product)


def test_is_obvious_non_match_keeps_actual_f3af_body_listing():
    product = {
        "id": "nikon-f3af",
        "category_id": "3323",
        "exclude_title_patterns": ["ai-s", "80mm", "200mm"],
    }

    assert not is_obvious_non_match("Nikon F3AF body film camera", product)


def test_should_expand_max_price_when_window_is_empty_or_clipped():
    assert should_expand_max_price([], 200)
    assert should_expand_max_price([199.0], 200)
    assert not should_expand_max_price([150.0], 200)


def test_round_price_bound_uses_reasonable_steps():
    assert round_price_bound(215) == 225
    assert round_price_bound(1260) == 1300
    assert round_price_bound(3650) == 3700
    assert round_price_bound(12600) == 13000


def test_extract_openrouter_message_text_handles_string_and_part_lists():
    assert extract_openrouter_message_text(
        {"choices": [{"message": {"content": "{\"indices\": [1, 3]}"}}]}
    ) == "{\"indices\": [1, 3]}"
    assert extract_openrouter_message_text(
        {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "{\"indices\": [0]}"},
                        ]
                    }
                }
            ]
        }
    ) == "{\"indices\": [0]}"
    assert extract_openrouter_message_text(
        {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "output_text", "content": "{\"indices\": [2]}"},
                        ]
                    }
                }
            ]
        }
    ) == "{\"indices\": [2]}"


def test_extract_openrouter_indices_accepts_object_and_legacy_list_payloads():
    assert extract_openrouter_indices(
        {"choices": [{"message": {"content": "{\"indices\": [1, 3]}"}}]}
    ) == [1, 3]
    assert extract_openrouter_indices(
        {"choices": [{"message": {"content": "[2, 4]"}}]}
    ) == [2, 4]


def test_strip_json_code_fence_handles_markdown_wrappers():
    assert strip_json_code_fence("```json\n{\"indices\": [0]}\n```") == "{\"indices\": [0]}"


def test_search_items_for_product_retries_with_higher_max_price(monkeypatch):
    product = {
        "id": "nikon-fg",
        "query": "Nikon FG body",
        "category_id": "3323",
        "min_price": 20,
        "max_price": 200,
    }
    search_calls = []

    def fake_search_items(token, browse_url, query, category_id, min_price, max_price):
        search_calls.append(max_price)
        if max_price <= 200:
            return []
        return [
            {"price": {"value": "215.00", "currency": "USD"}},
            {"price": {"value": "240.00", "currency": "USD"}},
        ]

    # search_items_for_product의 내부 호출은 정의 모듈(nikon_value.ebay)의
    # 네임스페이스를 거치므로 패치 대상도 그 모듈이어야 한다.
    monkeypatch.setattr(ebay, "search_items", fake_search_items)
    monkeypatch.setattr(ebay, "filter_items_with_rules", lambda items, product: items)

    items, effective_max_price = search_items_for_product("token", "browse", product)

    assert search_calls == [200.0, 400]
    assert effective_max_price == 400
    assert len(items) == 2


def test_search_items_for_product_retries_when_results_hit_upper_cap(monkeypatch):
    product = {
        "id": "nikon-zf",
        "query": "Nikon Zf body",
        "category_id": "31388",
        "min_price": 1200,
        "max_price": 2500,
    }
    search_calls = []

    def fake_search_items(token, browse_url, query, category_id, min_price, max_price):
        search_calls.append(max_price)
        if max_price <= 2500:
            return [
                {"price": {"value": "2499.00", "currency": "USD"}},
            ]
        return [
            {"price": {"value": "2499.00", "currency": "USD"}},
            {"price": {"value": "2899.00", "currency": "USD"}},
        ]

    monkeypatch.setattr(ebay, "search_items", fake_search_items)
    monkeypatch.setattr(ebay, "filter_items_with_rules", lambda items, product: items)

    items, effective_max_price = search_items_for_product("token", "browse", product)

    assert search_calls == [2500.0, 3800]
    assert effective_max_price == 3800
    assert len(items) == 2


def _zero_streak_product() -> dict:
    return {
        "id": "nikon-rare",
        "query": "Nikon rare body",
        "category_id": "3323",
        "min_price": 20,
        "max_price": 200,
    }


def test_should_expand_max_price_can_refuse_to_expand_on_empty_results():
    # 기본값은 기존 동작(빈 결과 = 상한이 낮다고 가정)을 유지한다.
    assert should_expand_max_price([], 200)
    assert not should_expand_max_price([], 200, expand_when_empty=False)
    # 결과가 상한에 붙어 있으면 건너뛰기 설정과 무관하게 확장한다.
    assert should_expand_max_price([199.0], 200, expand_when_empty=False)


def test_search_items_for_product_skips_expansion_for_persistently_empty_products(monkeypatch):
    product = _zero_streak_product()
    search_calls = []

    def fake_search_items(token, browse_url, query, category_id, min_price, max_price):
        search_calls.append(max_price)
        return []

    monkeypatch.setattr(ebay, "search_items", fake_search_items)
    monkeypatch.setattr(ebay, "filter_items_with_rules", lambda items, product: items)

    items, effective_max_price = search_items_for_product(
        "token", "browse", product, expand_when_empty=False
    )

    assert search_calls == [200.0]  # 확장 재검색 없음 (기본값이면 3회)
    assert effective_max_price == 200.0
    assert items == []


def test_skipping_empty_expansion_still_expands_once_listings_reappear(monkeypatch):
    """매물이 다시 나타나면 즉시 정상 동작으로 복귀해야 한다."""
    product = _zero_streak_product()
    search_calls = []

    def fake_search_items(token, browse_url, query, category_id, min_price, max_price):
        search_calls.append(max_price)
        if max_price <= 200:
            return [{"price": {"value": "199.00"}}]
        return [{"price": {"value": "199.00"}}, {"price": {"value": "260.00"}}]

    monkeypatch.setattr(ebay, "search_items", fake_search_items)
    monkeypatch.setattr(ebay, "filter_items_with_rules", lambda items, product: items)

    items, effective_max_price = search_items_for_product(
        "token", "browse", product, expand_when_empty=False
    )

    assert search_calls == [200.0, 400]
    assert effective_max_price == 400
    assert len(items) == 2


def test_count_trailing_zero_results_resets_as_soon_as_listings_appear():
    assert count_trailing_zero_results([]) == 0
    assert count_trailing_zero_results([{"date": "d1", "count": 3}]) == 0
    assert count_trailing_zero_results([
        {"date": "d1", "count": 0},
        {"date": "d2", "count": 3},
        {"date": "d3", "count": 0},
        {"date": "d4", "count": 0},
    ]) == 2
    assert count_trailing_zero_results([{"date": "d1", "count": 0}] * 5) == 5
    # count 키가 없거나 None인 항목도 0건으로 본다.
    assert count_trailing_zero_results([{"date": "d1"}, {"date": "d2", "count": None}]) == 2


def test_zero_result_streak_reads_the_product_history_file(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    (tmp_path / "products").mkdir()
    history = [{"date": f"d{i}", "count": 0} for i in range(ZERO_RESULT_STREAK_THRESHOLD)]
    (tmp_path / "products" / "nikon-rare.json").write_text(
        json.dumps(history), encoding="utf-8"
    )

    assert zero_result_streak("nikon-rare") >= ZERO_RESULT_STREAK_THRESHOLD
    assert zero_result_streak("nikon-unknown") == 0  # 히스토리가 없으면 정상 동작


def test_zero_result_streak_tolerates_a_corrupt_history_file(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    (tmp_path / "products").mkdir()
    (tmp_path / "products" / "nikon-rare.json").write_text("{broken", encoding="utf-8")

    assert zero_result_streak("nikon-rare") == 0
