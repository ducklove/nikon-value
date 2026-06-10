"""Behavioral tests for the pure helpers in the static site generator."""

import re
from datetime import date, timedelta

import pytest

from scripts.build_static_site import (
    build_robots,
    build_sitemap,
    compute_price_change,
    compute_stale_days,
    film_hotspot_style,
    format_change_percent,
    format_change_value,
    format_money,
    json_script,
    merge_catalog_with_config,
    render_money_span,
    sort_products,
)


def test_format_money_handles_none_integers_and_decimals():
    assert format_money(None) == '-'
    assert format_money(1234) == '$1,234'
    assert format_money(1234.0) == '$1,234'
    assert format_money(1234.56) == '$1,234.56'
    assert format_money(0.5) == '$0.50'


def test_render_money_span_without_value_has_no_data_attributes():
    html = render_money_span(None)
    assert 'data-money-usd' not in html
    assert 'money-value' in html
    assert '>-<' in html


def test_render_money_span_with_value_embeds_usd_amount_and_formatted_text():
    html = render_money_span(1234.5)
    assert 'data-money-usd="1234.5"' in html
    assert '$1,234.50' in html
    assert 'money-value' in html


def test_json_script_escapes_closing_script_tags():
    out = json_script({'html': 'before</script><script>after'})
    assert '</script>' not in out
    assert '<\\/script>' in out


def test_json_script_keeps_non_ascii_text_unescaped():
    out = json_script({'name_ko': '니콘 중고 시세'})
    assert '니콘 중고 시세' in out
    assert '\\u' not in out


def test_compute_stale_days_counts_days_since_update():
    today = date.today()
    assert compute_stale_days(today.isoformat()) == 0
    assert compute_stale_days((today - timedelta(days=3)).isoformat()) == 3


def _history(entries):
    return [{'date': day, 'median': median} for day, median in entries]


def test_compute_price_change_requires_two_valid_entries():
    assert compute_price_change([], 30) is None
    assert compute_price_change(_history([('2026-06-10', 100.0)]), 30) is None
    # Entries without a median do not count as valid.
    assert compute_price_change(
        _history([('2026-06-01', None), ('2026-06-10', 100.0)]), 30
    ) is None


def test_compute_price_change_uses_newest_entry_at_or_older_than_cutoff():
    history = _history([
        ('2026-04-01', 90.0),
        ('2026-05-11', 100.0),  # exactly `days` before the latest entry
        ('2026-05-20', 105.0),
        ('2026-06-10', 110.0),
    ])

    change = compute_price_change(history, 30)

    assert change['days'] == 30
    assert change['baseline_date'] == '2026-05-11'
    assert change['baseline_median'] == 100.0
    assert change['latest_date'] == '2026-06-10'
    assert change['latest_median'] == 110.0
    assert change['delta_value'] == pytest.approx(10.0)
    assert change['delta_pct'] == pytest.approx(10.0)
    assert change['actual_days'] == 30


def test_compute_price_change_falls_back_to_first_entry_for_short_history():
    history = _history([('2026-06-08', 100.0), ('2026-06-10', 95.0)])

    change = compute_price_change(history, 30)

    assert change['baseline_date'] == '2026-06-08'
    assert change['delta_value'] == pytest.approx(-5.0)
    assert change['delta_pct'] == pytest.approx(-5.0)
    assert change['actual_days'] == 2


def test_compute_price_change_returns_none_for_non_positive_baseline():
    history = _history([('2026-05-01', 0.0), ('2026-06-10', 50.0)])
    assert compute_price_change(history, 30) is None


def test_format_change_percent_signs():
    assert format_change_percent(None) == '-'
    assert format_change_percent({'delta_pct': 4.26}) == '+4.3%'
    assert format_change_percent({'delta_pct': -3.14}) == '-3.1%'


def test_format_change_value_signs():
    assert format_change_value(None) == '-'
    assert format_change_value({'delta_value': 12.5}) == '+$12.50'
    # Negative amounts keep the minus sign (rendered after the $ by format_money).
    assert format_change_value({'delta_value': -12.5}) == '$-12.50'


def test_sort_products_body_category_sorts_by_release_year_desc():
    products = [
        {'id': 'a', 'release_year': 1980},
        {'id': 'b'},  # missing year sorts last (treated as 0)
        {'id': 'c', 'release_year': 2000},
    ]

    result = sort_products(products, 'film-cameras')

    assert [p['id'] for p in result] == ['c', 'a', 'b']
    # The input list is not mutated.
    assert [p['id'] for p in products] == ['a', 'b', 'c']


def test_sort_products_lens_category_sorts_by_min_focal_length_asc():
    products = [
        {'id': 'a', 'focal_length_min': 85},
        {'id': 'b', 'focal_length_min': 24},
        {'id': 'c'},  # missing focal length sorts first (treated as 0)
    ]
    assert [p['id'] for p in sort_products(products, 'x-lenses')] == ['c', 'b', 'a']


def test_sort_products_other_categories_keep_original_order():
    products = [
        {'id': 'a', 'release_year': 1990, 'focal_length_min': 85},
        {'id': 'b', 'release_year': 2020, 'focal_length_min': 24},
    ]
    assert [p['id'] for p in sort_products(products, 'accessories')] == ['a', 'b']


def _config_catalog():
    return {
        'categories': [
            {
                'id': 'film-cameras',
                'name_ko': '필름 카메라',
                'name_en': 'Film cameras',
                'subcategories': [{'id': 'slr', 'name_ko': 'SLR'}],
                'products': [
                    {
                        'id': 'nikon-f3',
                        'name_ko': '니콘 F3',
                        'name_en': 'Nikon F3',
                        'query': 'Nikon F3 body',
                        'category_id': '3323',
                        'search_category_id': '625',
                        'min_price': 100,
                        'max_price': 900,
                        'release_year': 1980,
                    },
                    {
                        'id': 'nikon-fm2',
                        'name_ko': '니콘 FM2',
                        'name_en': 'Nikon FM2',
                        'query': 'Nikon FM2 body',
                        'category_id': '3323',
                        'min_price': 100,
                        'max_price': 800,
                        'is_rare': True,
                        'rarity_tier': 'A',
                    },
                ],
            }
        ]
    }


def _live_catalog():
    return {
        'updated': '2026-06-09',
        'exchange_rate': {'rate': 1350.5, 'base': 'USD', 'quote': 'KRW'},
        'categories': [
            {
                'id': 'film-cameras',
                'name_ko': '필름 카메라',
                'name_en': 'Film cameras',
                'products': [
                    {
                        'id': 'nikon-f3',
                        'name_ko': '니콘 F3',
                        'name_en': 'Nikon F3',
                        'median': 250.0,
                        'mean': 260.0,
                        'min': 120.0,
                        'max': 480.0,
                        'q1': 200.0,
                        'q3': 320.0,
                        'count': 12,
                        'count_filtered': 11,
                        'samples': [{'title': 'Nikon F3 HP', 'price': 250.0}],
                        # Stale rarity flags no longer present in the config.
                        'is_rare': True,
                        'rarity_tier': 'S',
                    }
                ],
            }
        ],
    }


def _merged_products():
    merged = merge_catalog_with_config(_live_catalog(), _config_catalog())
    products = merged['categories'][0]['products']
    return merged, {product['id']: product for product in products}


def test_merge_catalog_uses_metric_defaults_for_products_missing_from_live():
    _, products = _merged_products()
    fm2 = products['nikon-fm2']
    assert fm2['median'] is None
    assert fm2['mean'] is None
    assert fm2['count'] == 0
    assert fm2['count_filtered'] == 0
    assert fm2['samples'] == []


def test_merge_catalog_preserves_live_metrics_and_drops_search_config_keys():
    _, products = _merged_products()
    f3 = products['nikon-f3']
    assert f3['median'] == 250.0
    assert f3['count'] == 12
    assert f3['release_year'] == 1980
    for key in ('query', 'category_id', 'search_category_id', 'min_price', 'max_price'):
        assert key not in f3
        assert key not in products['nikon-fm2']


def test_merge_catalog_rarity_fields_follow_the_config():
    _, products = _merged_products()
    # Live product still carries rarity flags, but the config no longer does.
    assert 'is_rare' not in products['nikon-f3']
    assert 'rarity_tier' not in products['nikon-f3']
    # Config-declared rarity survives even without live data.
    assert products['nikon-fm2']['is_rare'] is True
    assert products['nikon-fm2']['rarity_tier'] == 'A'


def test_merge_catalog_takes_samples_from_live_and_passes_metadata_through():
    merged, products = _merged_products()
    assert products['nikon-f3']['samples'] == [{'title': 'Nikon F3 HP', 'price': 250.0}]
    assert merged['updated'] == '2026-06-09'
    assert merged['exchange_rate'] == {'rate': 1350.5, 'base': 'USD', 'quote': 'KRW'}
    category = merged['categories'][0]
    assert category['name_ko'] == '필름 카메라'
    assert category['subcategories'] == [{'id': 'slr', 'name_ko': 'SLR'}]


def _parse_style(style: str) -> dict[str, float]:
    parsed = {
        key: float(value)
        for key, value in re.findall(r'(left|top|width|height): ([-\d.]+)%;', style)
    }
    assert set(parsed) == {'left', 'top', 'width', 'height'}
    return parsed


def test_film_hotspot_style_grid_mode_uses_rows_and_cols():
    parsed = _parse_style(film_hotspot_style(1, 2, rows=4, cols=5))
    # 20%-wide columns and 25%-tall rows, inset by 1.1% on each side.
    assert parsed['left'] == pytest.approx(41.1, abs=1e-3)
    assert parsed['top'] == pytest.approx(26.1, abs=1e-3)
    assert parsed['width'] == pytest.approx(17.8, abs=1e-3)
    assert parsed['height'] == pytest.approx(22.8, abs=1e-3)


def test_film_hotspot_style_row_bounds_mode_uses_pixel_bands():
    style = film_hotspot_style(
        1, 0, rows=99, cols=2, row_bounds=[0, 100, 300], image_height=1000
    )
    parsed = _parse_style(style)
    # Band spans pixels 100..300 of a 1000px image: 10%..30%, inset by 1.1%.
    assert parsed['top'] == pytest.approx(11.1, abs=1e-3)
    assert parsed['height'] == pytest.approx(17.8, abs=1e-3)
    # Horizontal placement still comes from the column grid.
    assert parsed['left'] == pytest.approx(1.1, abs=1e-3)
    assert parsed['width'] == pytest.approx(47.8, abs=1e-3)


def test_film_hotspot_style_thin_band_gets_minimum_inset_and_height():
    style = film_hotspot_style(
        0, 0, rows=99, cols=1, row_bounds=[0, 10, 1000], image_height=1000
    )
    parsed = _parse_style(style)
    assert parsed['top'] == pytest.approx(0.2, abs=1e-3)
    assert parsed['height'] == pytest.approx(1.4, abs=1e-3)


def test_build_robots_without_base_url():
    assert build_robots('') == 'User-agent: *\nAllow: /\n'


def test_build_robots_with_base_url_appends_sitemap_line():
    out = build_robots('https://example.github.io/nikon-value')
    assert out == (
        'User-agent: *\n'
        'Allow: /\n'
        'Sitemap: https://example.github.io/nikon-value/sitemap.xml\n'
    )


def test_build_sitemap_is_empty_without_base_url():
    catalog = {'updated': '2026-06-09', 'categories': []}
    assert build_sitemap(catalog, '') == ''


def test_build_sitemap_lists_static_pages_and_every_product():
    catalog = {
        'updated': '2026-06-09',
        'categories': [
            {'id': 'c1', 'products': [{'id': 'nikon-f3'}, {'id': 'nikon-fm2'}]},
            {'id': 'c2', 'products': [{'id': 'nikkor-50mm'}]},
        ],
    }

    xml = build_sitemap(catalog, 'https://example.com')

    assert xml.count('<url>') == 5  # home + resources + 3 products
    assert '<loc>https://example.com/</loc>' in xml
    assert '<loc>https://example.com/resources.html</loc>' in xml
    assert '<loc>https://example.com/products/nikon-f3.html</loc>' in xml
    assert '<loc>https://example.com/products/nikon-fm2.html</loc>' in xml
    assert '<loc>https://example.com/products/nikkor-50mm.html</loc>' in xml
    assert xml.count('<lastmod>2026-06-09</lastmod>') == 5
