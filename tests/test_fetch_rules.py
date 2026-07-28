import json
import re

import pytest
import yaml

from nikon_value import ebay, storage
from nikon_value.paths import CONFIG_PATH
from scripts.fetch_prices import (
    VARIANT_GROUPS,
    ZERO_RESULT_STREAK_THRESHOLD,
    count_trailing_zero_results,
    extract_openrouter_indices,
    extract_openrouter_message_text,
    filter_items_with_rules,
    get_title_variant_group,
    is_obvious_non_match,
    is_variant_conflict,
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


# --------------------------------------------------------------------------- #
# 수동 렌즈 세대 필터 (get_title_variant_group / is_variant_conflict)
#
# AI / AI-S / non-AI / Series E는 겉모습이 비슷해 eBay 매물이 서로 섞이는데,
# 세대가 섞이면 시세 중앙값이 그대로 왜곡된다. 이 저장소에서 분기가 가장 많은
# 순수 함수이므로 세대 조합을 행렬로 훑는다.
# --------------------------------------------------------------------------- #

# 실제 eBay 타이틀에서 자주 보이는 세대 표기들
TITLE_AI_S = "Nikon Nikkor 50mm f/1.4 AI-S"
TITLE_AIS_SPELLED = "Nikon AIS Nikkor 50mm f/1.4"
TITLE_AI = "Nikon Nikkor 50mm f/1.4 AI"
TITLE_NON_AI = "Nikkor-S Auto 50mm f/1.4 Non-AI"
TITLE_NON_AI_SC = "Nikon Nikkor-S.C Auto 55mm f/1.2"
TITLE_SERIES_E = "Nikon Series E 50mm f/1.8"
TITLE_E_SERIES = "Nikon E Series 50mm f/1.8"
TITLE_AF = "AF Nikkor 50mm f/1.8D"
TITLE_UNMARKED = "Nikon Nikkor 50mm f/1.4"


@pytest.mark.parametrize(
    ("product_id", "expected"),
    [
        ("ai-s-50mm-f14", "ai-s"),
        ("series-e-50mm-f18", "series-e"),
        ("nikkor-auto-50mm-f14", "non-ai"),
        ("micro-nikkor-auto-55mm-f35", "non-ai"),
        ("noct-nikkor-58mm-f12", "non-ai"),
        ("nikkor-50mm-f14-ai", "ai"),
        # 접두사 규칙에 해당하지 않는 제품은 세대 필터를 적용하지 않는다.
        ("nikkor-z-50mm-f18-s", None),
        ("af-nikkor-50mm-f18d", None),
        ("nikon-fm2", None),
    ],
)
def test_get_title_variant_group_reads_the_product_id_prefix(product_id, expected):
    assert get_title_variant_group({"id": product_id}) == expected


def test_get_title_variant_group_tolerates_a_product_without_an_id():
    assert get_title_variant_group({}) is None


@pytest.mark.parametrize(
    ("title", "conflict"),
    [
        (TITLE_AI_S, False),
        (TITLE_AIS_SPELLED, False),  # 'AIS' 붙여쓰기도 같은 세대
        (TITLE_AI, True),
        (TITLE_NON_AI, True),
        (TITLE_SERIES_E, True),
        (TITLE_AF, True),
        (TITLE_UNMARKED, True),  # AI-S 표기가 없으면 다른 세대로 본다
        # AI-S 표기가 있어도 non-AI 표기가 함께 있으면 개조/혼동 매물로 제외
        ("Nikon Nikkor-S Auto 50mm f/1.4 AI-S converted", True),
    ],
)
def test_ai_s_product_rejects_other_generations(title, conflict):
    assert is_variant_conflict(title, {"id": "ai-s-50mm-f14"}) is conflict


@pytest.mark.parametrize(
    ("title", "conflict"),
    [
        (TITLE_AI, False),
        (TITLE_AI_S, True),
        (TITLE_AIS_SPELLED, True),
        (TITLE_NON_AI, True),
        (TITLE_SERIES_E, True),
        (TITLE_AF, True),
        (TITLE_UNMARKED, True),  # AI 표기가 없으면 세대를 확신할 수 없다
    ],
)
def test_ai_product_rejects_other_generations(title, conflict):
    assert is_variant_conflict(title, {"id": "nikkor-50mm-f14-ai"}) is conflict


@pytest.mark.parametrize(
    ("title", "conflict"),
    [
        (TITLE_NON_AI, False),
        (TITLE_NON_AI_SC, False),  # 'Nikkor-S.C Auto' 표기
        (TITLE_AI, True),  # AI 표기만 있고 non-AI 표기가 없으면 다른 세대
        (TITLE_AI_S, True),
        (TITLE_SERIES_E, True),
        (TITLE_AF, True),
        # non-AI 그룹은 세대 표기가 아예 없는 타이틀을 남긴다 — 초기 Auto 렌즈는
        # 판매자가 세대를 적지 않는 경우가 많아 AI 그룹과 판정이 다르다.
        (TITLE_UNMARKED, False),
    ],
)
def test_non_ai_product_rejects_other_generations(title, conflict):
    assert is_variant_conflict(title, {"id": "nikkor-auto-50mm-f14"}) is conflict


@pytest.mark.parametrize(
    ("title", "conflict"),
    [
        (TITLE_SERIES_E, False),
        (TITLE_E_SERIES, False),  # 'E Series' 어순도 같은 세대
        (TITLE_AI_S, True),
        (TITLE_NON_AI, True),
        (TITLE_AF, True),
        # Series E 표기가 있어도 AF 개조 매물은 제외
        ("Nikon Series E 50mm f/1.8 AF converted", True),
    ],
)
def test_series_e_product_rejects_other_generations(title, conflict):
    assert is_variant_conflict(title, {"id": "series-e-50mm-f18"}) is conflict


def test_products_without_a_variant_group_never_conflict():
    """Z 마운트·AF 렌즈나 바디는 세대 필터를 타지 않는다."""
    for title in (TITLE_AI_S, TITLE_NON_AI, TITLE_SERIES_E, TITLE_AF):
        assert is_variant_conflict(title, {"id": "nikon-fm2"}) is False


def _generation_from_name(name_en: str) -> str | None:
    """제품 표시명에서 읽어낸 수동 렌즈 세대 (판단 불가면 None)."""
    if "AI-S" in name_en:
        return "ai-s"
    if "Series E" in name_en:
        return "series-e"
    if re.search(r"\bAuto\b", name_en):
        return "non-ai"
    if re.search(r"\bAI\b", name_en):
        return "ai"
    return None


def test_config_product_ids_resolve_to_the_generation_their_name_declares():
    """설정(config/products.yaml)과 ID 접두사 규칙이 어긋나는지 감시한다.

    제품 ID로 판별한 세대가 표시명이 말하는 세대와 달라지면, 그 제품의 매물이
    통째로 잘못 걸러져 시세가 왜곡된다. 표시명만으로 세대를 알 수 없는 제품
    (예: Noct-Nikkor)은 판정 대상에서 제외한다.
    """
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    products = [p for category in config["categories"] for p in category.get("products", [])]

    grouped = {p["id"]: get_title_variant_group(p) for p in products if get_title_variant_group(p)}
    assert set(grouped.values()) == {"ai-s", "ai", "non-ai", "series-e"}, (
        "네 세대 그룹이 모두 설정에 존재해야 한다 — 접두사 규칙이 통째로 어긋났을 수 있다"
    )

    mismatched = {
        p["id"]: (grouped[p["id"]], _generation_from_name(p["name_en"]))
        for p in products
        if p["id"] in grouped
        and _generation_from_name(p["name_en"]) is not None
        and _generation_from_name(p["name_en"]) != grouped[p["id"]]
    }

    assert not mismatched, f"ID가 말하는 세대와 표시명이 다른 제품: {mismatched}"


# --------------------------------------------------------------------------- #
# is_obvious_non_match / filter_items_with_rules
# --------------------------------------------------------------------------- #


def test_is_obvious_non_match_drops_parts_and_accessory_listings():
    product = {"id": "nikon-fm2", "category_id": "3323"}

    assert is_obvious_non_match("Nikon FM2 body for parts", product)
    assert is_obvious_non_match("Nikon FM2 camera case", product)
    assert not is_obvious_non_match("Nikon FM2 black body", product)


def test_accessory_products_keep_the_accessory_words_they_are_about():
    """뷰파인더·포커싱 스크린 자체를 파는 제품은 그 단어로 제외하면 안 된다."""
    accessory = {"id": "nikon-dw-3", "category_id": "78997", "product_type": "accessory"}
    body = {"id": "nikon-f3", "category_id": "3323"}

    assert not is_obvious_non_match("Nikon DW-3 waist level viewfinder", accessory)
    assert is_obvious_non_match("Nikon DW-3 waist level viewfinder", body)
    # 액세서리라도 부품·고장 매물은 그대로 제외한다.
    assert is_obvious_non_match("Nikon DW-3 finder for parts", accessory)


def test_is_obvious_non_match_drops_lens_hoods():
    product = {"id": "ai-s-50mm-f14", "category_id": "78997"}

    assert is_obvious_non_match("Nikon HS-9 Lens Hood for 50mm f/1.4 AI-S", product)
    assert is_obvious_non_match("Nikon HB-32 hood", product)


def test_is_obvious_non_match_drops_a_different_lens_generation():
    product = {"id": "ai-s-50mm-f14", "category_id": "78997"}

    assert is_obvious_non_match("Nikkor-S Auto 50mm f/1.4 Non-AI", product)
    assert not is_obvious_non_match("Nikon Nikkor 50mm f/1.4 AI-S", product)


def test_camera_body_products_drop_listings_bundled_with_a_lens():
    product = {"id": "nikon-fm2", "category_id": "3323"}
    lens = {"id": "ai-s-50mm-f14", "category_id": "78997"}

    assert is_obvious_non_match("Nikon FM2 with 50mm kit lens", product)
    # 같은 문구라도 렌즈 카테고리 제품에는 바디 전용 규칙을 적용하지 않는다.
    assert not is_obvious_non_match("Nikon Nikkor 50mm f/1.4 AI-S kit lens", lens)


def test_filter_items_with_rules_removes_only_the_obvious_non_matches():
    product = {"id": "nikon-fm2", "category_id": "3323"}
    items = [
        {"title": "Nikon FM2 black body"},
        {"title": "Nikon FM2 body for parts"},
        {"title": "Nikon FM2n chrome body"},
    ]

    filtered = filter_items_with_rules(items, product)

    assert [item["title"] for item in filtered] == [
        "Nikon FM2 black body",
        "Nikon FM2n chrome body",
    ]


def test_filter_items_with_rules_keeps_the_original_set_when_everything_is_filtered():
    """전부 걸러지면 규칙이 과했다고 보고 원본을 LLM 단계로 넘긴다."""
    product = {"id": "nikon-fm2", "category_id": "3323"}
    items = [{"title": "Nikon FM2 for parts"}, {"title": "Nikon FM2 broken"}]

    assert filter_items_with_rules(items, product) is items


def test_filter_items_with_rules_passes_an_empty_list_through():
    assert filter_items_with_rules([], {"id": "nikon-fm2", "category_id": "3323"}) == []


# --- variant_group 명시 지정 -------------------------------------------------


def test_explicit_variant_group_overrides_the_id_heuristic():
    """ID 접두사 규칙에 걸리지 않는 제품도 설정으로 세대를 지정할 수 있다."""
    product = {"id": "gn-auto-nikkor-45mm-f28", "variant_group": "non-ai"}

    assert get_title_variant_group(product) == "non-ai"
    assert get_title_variant_group({"id": "gn-auto-nikkor-45mm-f28"}) is None


def test_unknown_variant_group_falls_back_to_the_id_heuristic():
    """오타 등으로 알 수 없는 값이 들어오면 무시하고 기존 추정을 쓴다."""
    assert get_title_variant_group({"id": "ai-s-nikkor-50mm-f14", "variant_group": "typo"}) == "ai-s"
    assert get_title_variant_group({"id": "unknown-lens", "variant_group": "typo"}) is None


def test_configured_variant_groups_are_all_known_values():
    """config의 variant_group 값이 is_variant_conflict가 아는 그룹이어야 한다."""
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    configured = {
        product["id"]: product["variant_group"]
        for category in config["categories"]
        for product in category["products"]
        if product.get("variant_group")
    }

    assert configured, "variant_group을 지정한 제품이 하나도 없다"
    unknown = {pid: g for pid, g in configured.items() if g not in VARIANT_GROUPS}
    assert not unknown, f"알 수 없는 variant_group: {unknown}"


def test_explicitly_grouped_products_keep_their_own_generation_listings():
    """명시 지정이 정상 매물을 걸러내면 안 된다 (과필터링 회귀 방지).

    카탈로그의 실제 샘플 타이틀로 검증한다. 세대 표기가 판매자마다 엇갈리는
    제품(예: Ai-P 45mm f/2.8P)에 그룹을 지정하면 정상 매물이 잘리므로,
    그런 제품은 애초에 지정하지 않는다는 판단을 이 테스트가 고정한다.
    """
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    catalog = json.loads((CONFIG_PATH.parent.parent / "data" / "catalog.json").read_text(encoding="utf-8"))
    live = {
        product["id"]: product
        for category in catalog["categories"]
        for product in category["products"]
    }

    dropped = []
    for category in config["categories"]:
        for product in category["products"]:
            if not product.get("variant_group"):
                continue
            for sample in live.get(product["id"], {}).get("samples", []):
                if is_variant_conflict(sample["title"], product):
                    dropped.append((product["id"], sample["title"]))

    assert not dropped, f"명시 지정이 자기 세대 매물을 걸러낸다: {dropped}"


# --- 검색 파라미터 구성 -------------------------------------------------------


def test_build_search_filter_matches_the_production_constraint_set():
    """리팩터링 전 하드코딩되어 있던 filter 문자열을 그대로 재현한다."""
    assert ebay.build_search_filter(20, 150) == (
        "conditionIds:{3000},"
        "price:[20..150],"
        "priceCurrency:USD,"
        "deliveryCountry:KR,"
        "buyingOptions:{FIXED_PRICE}"
    )


def test_relaxing_a_constraint_drops_only_that_clause():
    relaxed = ebay.relax(ebay.DEFAULT_SEARCH_CONSTRAINTS, delivery_country=None)
    search_filter = ebay.build_search_filter(20, 150, relaxed)

    assert "deliveryCountry" not in search_filter
    assert "conditionIds:{3000}" in search_filter
    assert "price:[20..150]" in search_filter


def test_an_unconstrained_search_produces_no_filter_and_no_category():
    params = ebay.build_search_params(
        "Nikon FE10", "3323", 20, 150, constraints=ebay.UNCONSTRAINED_SEARCH
    )

    assert "filter" not in params
    assert "category_ids" not in params


def test_search_params_omit_the_category_when_the_product_has_none():
    params = ebay.build_search_params("Nikon FE10", None, 20, 150)
    assert "category_ids" not in params


# --- count == 0 의 의미 -------------------------------------------------------
#
# "count == 0 이면 eBay 검색이 0건" 추론이 코드에서 실제로 성립하는지 고정한다.
# 규칙 필터·LLM 필터에는 "전부 걸러내면 원본 유지" 폴백이 있으므로, 비어 있지
# 않은 입력이 비어 있는 출력이 되는 경로는 없어야 한다.


def test_the_rule_filter_can_never_turn_a_non_empty_set_into_an_empty_one():
    product = {"id": "nikon-fm2", "category_id": "3323"}
    items = [{"title": "for parts"}, {"title": "broken"}, {"title": "empty box"}]

    assert filter_items_with_rules(items, product)


def test_the_llm_filter_can_never_turn_a_non_empty_set_into_an_empty_one(monkeypatch):
    from nikon_value import llm

    product = {"id": "nikon-fm2", "name_en": "Nikon FM2", "query": "Nikon FM2 body"}
    items = [{"title": "Nikon FM2 body"}, {"title": "Nikon FM2n body"}]

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"indices": []}'}}]}

    # LLM이 전부 탈락시켜도(폴백 1) 원본이 살아남는다.
    monkeypatch.setattr(llm.requests, "post", lambda *a, **kw: _Resp())
    assert llm.filter_items_with_llm(items, product, "key") is items

    # 호출 자체가 실패해도(폴백 2) 원본이 살아남는다.
    def _boom(*a, **kw):
        raise ConnectionError("down")

    monkeypatch.setattr(llm.requests, "post", _boom)
    assert llm.filter_items_with_llm(items, product, "key") is items


def test_the_only_way_a_non_empty_search_yields_count_zero_is_missing_prices():
    """유일한 반례: 매물은 있는데 전부 가격이 없는 경우.

    `buyingOptions:{FIXED_PRICE}` 필터가 걸려 있어 실무에서는 거의 나오지
    않지만 코드상으로는 가능한 경로이므로, baseline 프로브가 이 경우를
    "검색은 매물을 반환했다"로 구분해 준다.
    """
    from nikon_value.stats import compute_stats

    priced = [{"title": "Nikon FM2", "price": {"value": "150.00"}}]
    unpriced = [{"title": "Nikon FM2"}]

    assert compute_stats(ebay.collect_prices(priced))["count"] == 1
    assert compute_stats(ebay.collect_prices(unpriced))["count"] == 0
