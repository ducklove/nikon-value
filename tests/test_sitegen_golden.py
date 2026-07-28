"""Golden-file tests pinning the exact output of the sitegen page builders.

Every document is rendered from a small, fully in-test fixture catalog (no repo
data) with the clock and environment knobs frozen, then compared byte-for-byte
against the files in tests/golden/. They exist so the planned Jinja2 template
migration can be verified to produce identical output.

To regenerate the golden files after an intentional output change, run:

    UPDATE_GOLDENS=1 pytest tests/test_sitegen_golden.py

and review the resulting diff under tests/golden/ before committing.

Determinism notes:
- ``compute_stale_days`` calls ``datetime.now()``; ``pages.py`` imports it by
  name, so the reference inside ``nikon_value.sitegen.pages`` is monkeypatched
  to return 0 (no stale banner, no dependence on today's date).
- ``head_block`` embeds ``api_base_url()`` which reads the
  ``NIKON_API_BASE_URL`` environment variable; it is deleted so the built-in
  default is always used.
- Nothing else in the sitegen package reads the clock at render time
  (``merge_catalog_with_config`` falls back to ``date.today()`` only when the
  live catalog lacks ``updated``, and this fixture always provides it).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from nikon_value.sitegen import pages

BASE_URL = 'https://example.test/nikon-value'
GOLDEN_DIR = Path(__file__).resolve().parent / 'golden'
GOLDEN_FILES = (
    'home.html',
    'product-nikon-z9.html',
    'product-nikon-z5.html',
    # 비교 페이지는 noindex 메타·비교 대상 JSON 페이로드·스크립트 로드 순서를
    # 한꺼번에 고정해야 하는 문서라 골든으로 관리한다.
    'compare.html',
    'resources.html',
    '404.html',
    'sitemap.xml',
    'robots.txt',
)


def _make_catalog() -> dict[str, Any]:
    # NOTE: the category id 'f-mount-dslr' is load-bearing. It is the only id for
    # which should_show_home_catalog_product() keeps count-0 products visible on
    # the home page, so it lets the golden home page exercise both a fully
    # populated card (nikon-z9) and an empty "not scraped yet" card (nikon-z5).
    # Under any other category id the empty product would be filtered out.
    return {
        'updated': '2026-01-15',
        'exchange_rate': {
            'base': 'USD',
            'quote': 'KRW',
            'rate': 1396.55,
            'reference_date': '2026-01-14',
            'source': 'ECB reference rates',
        },
        'categories': [
            {
                'id': 'f-mount-dslr',
                'name_ko': 'F마운트 DSLR',
                'name_en': 'F-Mount DSLR',
                'subcategories': [
                    {'id': 'flagship', 'name_ko': '플래그십', 'name_en': 'Flagship', 'sort_order': 1},
                ],
                'products': [
                    {
                        'id': 'nikon-z9',
                        'name_ko': '니콘 Z9',
                        'name_en': 'Nikon Z9',
                        'release_year': 2021,
                        'subcategory': 'flagship',
                        'median': 5250.0,
                        'mean': 5305.5,
                        'min': 4400.0,
                        'max': 6100.0,
                        'q1': 5000.0,
                        'q3': 5550.0,
                        'count': 22,
                        'count_filtered': 20,
                        'is_rare': True,
                        'rarity_tier': 'S',
                        'rarity_sort': 10,
                        'rarity_price_hint': '$5,000-6,100',
                        'rarity_note': '단종 후 유통 물량이 줄어 상태 좋은 개체는 빠르게 소진됩니다.',
                        'samples': [
                            {
                                'title': 'Nikon Z9 45.7MP Mirrorless Camera Body',
                                'price': 5199.0,
                                'currency': 'USD',
                                'condition': 'Used',
                                'image': 'https://i.ebayimg.com/images/g/golden/s-l640.jpg',
                                'url': 'https://www.ebay.com/itm/123456789012',
                            },
                        ],
                        'deals': [
                            {
                                'title': 'Nikon Z9 Body — low shutter count, boxed',
                                'price': 3990.0,
                                'discount_pct': 24.0,
                                'image': 'https://i.ebayimg.com/images/g/golden-deal/s-l225.jpg',
                                'url': 'https://www.ebay.com/itm/987654321098',
                            },
                        ],
                    },
                    {
                        # Never-scraped product: metric defaults exactly as
                        # merge_catalog_with_config() produces them.
                        'id': 'nikon-z5',
                        'name_ko': '니콘 Z5',
                        'name_en': 'Nikon Z5',
                        'median': None,
                        'mean': None,
                        'min': None,
                        'max': None,
                        'q1': None,
                        'q3': None,
                        'count': 0,
                        'count_filtered': 0,
                        'samples': [],
                        'deals': [],
                    },
                ],
            },
        ],
    }


def _make_histories() -> dict[str, list[dict[str, Any]]]:
    return {
        'nikon-z9': [
            {'date': '2025-12-05', 'median': 5450.0, 'q1': 5200.0, 'q3': 5700.0, 'count': 18},
            {'date': '2025-12-12', 'median': 5420.0, 'q1': 5150.0, 'q3': 5680.0, 'count': 17},
            # A failed-scrape day: median None must be filtered out by
            # compute_price_change() but still rendered as '-' in the table.
            {'date': '2025-12-19', 'median': None, 'q1': None, 'q3': None, 'count': 0},
            {'date': '2025-12-26', 'median': 5380.0, 'q1': 5100.0, 'q3': 5650.0, 'count': 19},
            {'date': '2026-01-07', 'median': 5300.0, 'q1': 5050.0, 'q3': 5600.0, 'count': 21},
            {'date': '2026-01-14', 'median': 5250.0, 'q1': 5000.0, 'q3': 5550.0, 'count': 22},
        ],
        'nikon-z5': [],
    }


@pytest.fixture
def freeze_rendering_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin everything the page builders read from the clock or environment."""
    monkeypatch.setattr('nikon_value.sitegen.pages.compute_stale_days', lambda updated: 0)
    monkeypatch.delenv('NIKON_API_BASE_URL', raising=False)


def _render_all() -> dict[str, str]:
    """Render every golden-tracked document (freeze_rendering_inputs required)."""
    catalog = _make_catalog()
    histories = _make_histories()
    category = catalog['categories'][0]
    products = {product['id']: product for product in category['products']}
    rendered = {
        'home.html': pages.build_home_page(catalog, BASE_URL, histories),
        'compare.html': pages.build_compare_page(catalog, BASE_URL),
        'resources.html': pages.build_resources_page(BASE_URL),
        '404.html': pages.build_404_page(BASE_URL),
        'sitemap.xml': pages.build_sitemap(catalog, BASE_URL),
        'robots.txt': pages.build_robots(BASE_URL),
    }
    for product_id in ('nikon-z9', 'nikon-z5'):
        rendered[f'product-{product_id}.html'] = pages.build_product_page(
            products[product_id],
            category,
            catalog['updated'],
            histories[product_id],
            catalog['exchange_rate'],
            BASE_URL,
        )
    return rendered


def _updating_goldens() -> bool:
    return os.environ.get('UPDATE_GOLDENS', '') not in ('', '0')


@pytest.mark.usefixtures('freeze_rendering_inputs')
@pytest.mark.parametrize('name', GOLDEN_FILES)
def test_rendered_document_matches_golden(name: str) -> None:
    content = _render_all()[name]
    golden_path = GOLDEN_DIR / name

    if _updating_goldens():
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(content, encoding='utf-8')
    if not golden_path.exists():
        pytest.fail(
            f'Golden file {golden_path} is missing. '
            'Create it with: UPDATE_GOLDENS=1 pytest tests/test_sitegen_golden.py'
        )

    assert content == golden_path.read_text(encoding='utf-8'), (
        f'{name} no longer matches tests/golden/{name}. If the output change is '
        'intentional, regenerate with: UPDATE_GOLDENS=1 pytest tests/test_sitegen_golden.py'
    )


@pytest.mark.usefixtures('freeze_rendering_inputs')
def test_build_home_page_twice_yields_identical_bytes() -> None:
    catalog = _make_catalog()
    histories = _make_histories()

    first = pages.build_home_page(catalog, BASE_URL, histories)
    second = pages.build_home_page(catalog, BASE_URL, histories)

    assert first.encode('utf-8') == second.encode('utf-8')


@pytest.mark.usefixtures('freeze_rendering_inputs')
def test_home_cards_data_covers_both_products() -> None:
    """Guards the fixture itself: both card states must stay on the home page."""
    html = pages.build_home_page(_make_catalog(), BASE_URL, _make_histories())

    match = re.search(
        r'<script id="cards-data" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert match is not None
    cards = {card['id']: card for card in json.loads(match.group(1))}

    assert list(cards) == ['nikon-z9', 'nikon-z5']
    assert cards['nikon-z9']['is_rare'] is True
    assert cards['nikon-z9']['delta_pct'] == -3.1
    assert cards['nikon-z5']['median'] is None
    assert cards['nikon-z5']['count'] == 0
