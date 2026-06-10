"""Full-build invariants for scripts/build_static_site.py against real repo data.

This is the pytest version of the CI smoke check: the site is built once per
session into a temp directory, and the tests assert structural invariants that
must keep holding through the planned Jinja2 template migration.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = 'https://example.test/nikon-value'


def _script_json(html: str, element_id: str) -> Any:
    match = re.search(
        rf'<script id="{element_id}" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert match is not None, f'missing <script id="{element_id}"> JSON payload'
    return json.loads(match.group(1))


@pytest.fixture(scope='session')
def site_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp('static-site')
    result = subprocess.run(
        [
            sys.executable,
            'scripts/build_static_site.py',
            '--output',
            str(output),
            '--base-url',
            BASE_URL,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f'build failed:\n{result.stdout}\n{result.stderr}'
    return output


@pytest.fixture(scope='session')
def config_product_ids() -> list[str]:
    config = yaml.safe_load((REPO_ROOT / 'config' / 'products.yaml').read_text(encoding='utf-8'))
    return [
        product['id']
        for category in config['categories']
        for product in category.get('products', [])
    ]


def test_home_cards_data_payload(site_dir: Path) -> None:
    html = (site_dir / 'index.html').read_text(encoding='utf-8')
    cards = _script_json(html, 'cards-data')

    assert len(cards) >= 300
    for entry in cards:
        assert entry.keys() >= {'id', 'name_ko', 'median', 'count'}

    ids = [entry['id'] for entry in cards]
    assert len(ids) == len(set(ids)), 'duplicate product ids in cards-data'


def test_home_declares_api_base_meta(site_dir: Path) -> None:
    html = (site_dir / 'index.html').read_text(encoding='utf-8')
    assert '<meta name="nikon-api-base"' in html


def test_product_pages_cover_every_config_product(site_dir: Path, config_product_ids: list[str]) -> None:
    pages = list((site_dir / 'products').glob('*.html'))
    assert len(pages) == len(config_product_ids)


def test_sitemap_lists_home_resources_and_every_product(site_dir: Path, config_product_ids: list[str]) -> None:
    xml = (site_dir / 'sitemap.xml').read_text(encoding='utf-8')
    locs = re.findall(r'<loc>(.*?)</loc>', xml)

    assert len(locs) == len(config_product_ids) + 2
    assert all(loc.startswith(BASE_URL) for loc in locs)


def test_robots_points_to_sitemap(site_dir: Path) -> None:
    robots = (site_dir / 'robots.txt').read_text(encoding='utf-8')
    assert f'Sitemap: {BASE_URL}/sitemap.xml' in robots


def test_sample_product_page_structure(site_dir: Path, config_product_ids: list[str]) -> None:
    html = (site_dir / 'products' / f'{config_product_ids[0]}.html').read_text(encoding='utf-8')

    assert 'id="price-chart"' in html
    history = _script_json(html, 'history-data')
    assert isinstance(history, list)
    assert '<meta name="nikon-api-base"' in html
