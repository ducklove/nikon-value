"""Behavioral tests for the pure stats/price helpers in the fetch pipeline."""

import math

import pytest

from scripts.fetch_prices import (
    collect_prices,
    compute_stats,
    extract_openrouter_indices,
    extract_openrouter_message_text,
    extract_price,
    extract_sample_listings,
    round_price_bound,
    should_expand_max_price,
    strip_json_code_fence,
)


def test_compute_stats_empty_prices_returns_null_stats():
    assert compute_stats([]) == {
        "median": None,
        "mean": None,
        "min": None,
        "max": None,
        "q1": None,
        "q3": None,
        "count": 0,
        "count_filtered": 0,
    }


def test_compute_stats_small_sample_skips_iqr_filter():
    # n < 4: no outlier removal, q1/q3 collapse to min/max.
    assert compute_stats([120.0, 80.0, 100.0]) == {
        "median": 100.0,
        "mean": 100.0,
        "min": 80.0,
        "max": 120.0,
        "q1": 80.0,
        "q3": 120.0,
        "count": 3,
        "count_filtered": 3,
    }


def test_compute_stats_single_price():
    stats = compute_stats([42.0])
    assert stats["median"] == 42.0
    assert stats["q1"] == 42.0
    assert stats["q3"] == 42.0
    assert stats["count"] == 1
    assert stats["count_filtered"] == 1


def test_compute_stats_excludes_extreme_outlier_via_iqr():
    prices = [10000.0, 100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
    assert compute_stats(prices) == {
        "median": 103.0,
        "mean": 103.0,
        "min": 100.0,
        "max": 106.0,
        "q1": 101.5,
        "q3": 105.5,
        "count": 8,
        "count_filtered": 7,
    }


def test_compute_stats_falls_back_to_full_set_when_filter_removes_everything():
    # For finite inputs the IQR window always retains at least one point, so
    # NaN prices are the one input that exercises the "filter removed
    # everything" fallback: every comparison against the NaN bounds is False.
    stats = compute_stats([float("nan")] * 4)
    assert stats["count"] == 4
    assert stats["count_filtered"] == 4  # fallback kept the full set
    assert math.isnan(stats["median"])


def test_compute_stats_rounds_results_to_two_decimals():
    stats = compute_stats([10.111, 10.222, 10.999])
    assert stats["median"] == 10.22
    assert stats["mean"] == 10.44
    assert stats["min"] == 10.11
    assert stats["max"] == 11.0


def test_extract_price_uses_price_value_when_no_shipping():
    assert extract_price({"price": {"value": "215.00", "currency": "USD"}}) == 215.0


def test_extract_price_adds_only_first_shipping_option_cost():
    item = {
        "price": {"value": "100.50"},
        "shippingOptions": [
            {"shippingCost": {"value": "12.25"}},
            {"shippingCost": {"value": "99.99"}},  # later options are ignored
        ],
    }
    assert extract_price(item) == 112.75


def test_extract_price_returns_none_when_price_is_missing():
    assert extract_price({}) is None
    assert extract_price({"price": {}}) is None


def test_extract_price_ignores_empty_shipping_cost_values():
    base = {"price": {"value": "50"}}
    assert extract_price({**base, "shippingOptions": [{"shippingCost": {"value": None}}]}) == 50.0
    assert extract_price({**base, "shippingOptions": [{}]}) == 50.0


def test_collect_prices_skips_items_without_price():
    items = [
        {"price": {"value": "10.00"}},
        {"title": "listing without price info"},
        {"price": {}},
        {"price": {"value": "20.50"}, "shippingOptions": [{"shippingCost": {"value": "4.50"}}]},
    ]
    assert collect_prices(items) == [10.0, 25.0]


def _listing(i: int, *, priced: bool = True) -> dict:
    item = {
        "title": f"Listing {i}",
        "condition": "USED",
        "thumbnailImages": [{"imageUrl": f"https://img.example/{i}.jpg"}],
        "itemWebUrl": f"https://ebay.example/itm/{i}",
    }
    if priced:
        item["price"] = {"value": f"{100 + i}.00", "currency": "USD"}
    return item


def test_extract_sample_listings_returns_five_centered_samples():
    items = [_listing(i) for i in range(9)]

    samples = extract_sample_listings(items)

    # 9 priced items -> a window of 5 around the middle of the sorted-by-input list.
    assert [s["title"] for s in samples] == [f"Listing {i}" for i in range(2, 7)]
    assert samples[0] == {
        "title": "Listing 2",
        "price": 102.0,
        "currency": "USD",
        "condition": "USED",
        "image": "https://img.example/2.jpg",
        "url": "https://ebay.example/itm/2",
    }


def test_extract_sample_listings_skips_unpriced_items():
    items = [
        _listing(0),
        _listing(1, priced=False),
        _listing(2),
        _listing(3, priced=False),
        _listing(4),
    ]

    samples = extract_sample_listings(items)

    assert [s["title"] for s in samples] == ["Listing 0", "Listing 2", "Listing 4"]
    assert [s["price"] for s in samples] == [100.0, 102.0, 104.0]


def test_round_price_bound_step_boundaries():
    # < 500 rounds up in steps of 25; the result of 499 lands exactly on 500.
    assert round_price_bound(499) == 500
    # >= 500 switches to steps of 50.
    assert round_price_bound(500) == 500
    assert round_price_bound(501) == 550
    assert round_price_bound(1999) == 2000
    # >= 2000 switches to steps of 100.
    assert round_price_bound(2000) == 2000
    assert round_price_bound(2001) == 2100
    assert round_price_bound(9999) == 10000
    # >= 10000 switches to steps of 500.
    assert round_price_bound(10000) == 10000
    assert round_price_bound(10001) == 10500
    assert round_price_bound(19999) == 20000
    # >= 20000 switches to steps of 1000.
    assert round_price_bound(20000) == 20000
    assert round_price_bound(20001) == 21000


def test_should_expand_max_price_trigger_threshold_is_inclusive():
    assert should_expand_max_price([], 200)
    # The trigger ratio is 0.98 of the cap and the comparison is inclusive.
    assert should_expand_max_price([196.0], 200.0)
    assert not should_expand_max_price([195.99], 200.0)


def test_strip_json_code_fence_handles_bare_fence_and_plain_text():
    # Cases beyond the ```json wrapper covered in test_fetch_rules.py.
    assert strip_json_code_fence("```\n[1, 2]\n```") == "[1, 2]"
    assert strip_json_code_fence('  {"indices": []}  ') == '{"indices": []}'


def test_extract_openrouter_indices_parses_fenced_json_content():
    data = {
        "choices": [
            {"message": {"content": "```json\n{\"indices\": [0, 2]}\n```"}}
        ]
    }
    assert extract_openrouter_indices(data) == [0, 2]


def test_extract_openrouter_indices_rejects_payloads_without_an_indices_list():
    with pytest.raises(ValueError):
        extract_openrouter_indices(
            {"choices": [{"message": {"content": "{\"foo\": [0]}"}}]}
        )
    with pytest.raises(ValueError):
        extract_openrouter_indices({"choices": [{"message": {"content": "42"}}]})


def test_extract_openrouter_message_text_rejects_parts_without_text():
    with pytest.raises(ValueError):
        extract_openrouter_message_text(
            {"choices": [{"message": {"content": [{"type": "image"}]}}]}
        )
