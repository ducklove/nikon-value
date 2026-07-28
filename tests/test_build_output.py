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
from urllib.parse import unquote, urlparse

import pytest
import yaml

from nikon_value.sitegen.pages import INLINE_HISTORY_POINTS

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_URL = 'https://example.test/nikon-value'

# src="..." / href="..." 값 추출 (생성되는 HTML은 항상 큰따옴표를 쓴다).
_LOCAL_REF_RE = re.compile(r'(?:src|href)="([^"]*)"')
# 스킴이 붙은 절대 URL (https:, mailto:, data:, javascript: ...)
_SCHEME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.\-]*:')
# products/*.html 전수 검사는 비싸므로 균등 간격으로 샘플링한다.
PRODUCT_PAGE_SAMPLE_STEP = 25


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


def test_artifact_is_self_contained_for_pages_deploy(site_dir: Path, config_product_ids: list[str]) -> None:
    """artifact 배포 전환 후 루트 소스로 서빙되던 공개 파일들이 산출물에 포함돼야 한다."""
    # OAuth 로그인 복귀 페이지
    assert (site_dir / 'auth-complete.html').exists()
    # API 서버(제품 검증·가격 알림)가 읽는 catalog.json
    catalog = json.loads((site_dir / 'data' / 'catalog.json').read_text(encoding='utf-8'))
    assert catalog.get('categories')
    # 관심목록 가치 대시보드가 fetch하는 제품 히스토리
    histories = list((site_dir / 'data' / 'products').glob('*.json'))
    assert len(histories) >= len(config_product_ids) * 0.9
    # 로컬 전용 admin은 공개 산출물에 포함되지 않아야 한다
    assert not (site_dir / 'admin.html').exists()
    assert not (site_dir / 'js' / 'admin.js').exists()


def _local_refs(html: str) -> list[str]:
    """HTML에서 산출물 내부를 가리키는 src/href 값만 뽑는다.

    제외 대상: 외부 URL(`https:` 등 스킴 포함, `//`로 시작하는 프로토콜 상대 경로),
    페이지 내 앵커(`#`), 빈 값.
    """
    refs = []
    for raw in _LOCAL_REF_RE.findall(html):
        ref = raw.strip()
        if not ref or ref.startswith(('#', '//')) or _SCHEME_RE.match(ref):
            continue
        refs.append(ref)
    return refs


def _resolve_ref(html_path: Path, ref: str) -> Path:
    """상대 참조를 산출물 실제 경로로 정규화한다 (쿼리스트링·프래그먼트 제거)."""
    path = urlparse(ref).path
    if path.endswith('/'):
        path += 'index.html'
    return (html_path.parent / unquote(path)).resolve()


def _missing_refs(site_dir: Path, html_path: Path) -> list[str]:
    html = html_path.read_text(encoding='utf-8')
    return sorted(
        {
            ref
            for ref in _local_refs(html)
            if urlparse(ref).path and not _resolve_ref(html_path, ref).exists()
        }
    )


def test_every_local_reference_exists_in_artifact(site_dir: Path) -> None:
    """생성된 HTML의 로컬 src/href가 모두 산출물에 존재해야 한다.

    copy_assets()가 페이지에서 참조하는 에셋을 빠뜨리면 배포된 사이트에서
    이미지·스크립트가 깨진다. 특정 파일 하나가 아니라 결함 유형 전체를 막는다.
    index/resources/404는 전수 검사하고, products/*.html은 균등 간격 샘플링한다.
    """
    pages = [site_dir / 'index.html', site_dir / 'resources.html', site_dir / '404.html']
    product_pages = sorted((site_dir / 'products').glob('*.html'))
    assert product_pages, 'products/*.html이 생성되지 않았다'
    pages.extend(product_pages[::PRODUCT_PAGE_SAMPLE_STEP])

    broken = {}
    for page in pages:
        assert page.exists(), f'{page.name}이 생성되지 않았다'
        missing = _missing_refs(site_dir, page)
        if missing:
            broken[str(page.relative_to(site_dir))] = missing

    assert not broken, '산출물에 없는 로컬 참조가 있다:\n' + '\n'.join(
        f'  {page}: {", ".join(refs)}' for page, refs in sorted(broken.items())
    )


def test_product_page_inlines_only_recent_history(site_dir: Path, config_product_ids: list[str]) -> None:
    """인라인 history-data는 최근 구간만 담고, 전체는 data-history-url로 넘긴다."""
    checked = 0
    for product_id in config_product_ids:
        page = site_dir / 'products' / f'{product_id}.html'
        html = page.read_text(encoding='utf-8')
        inline = _script_json(html, 'history-data')
        assert len(inline) <= INLINE_HISTORY_POINTS, f'{product_id}: 인라인 히스토리가 너무 길다'
        assert f'data-history-url="../data/products/{product_id}.json"' in html

        full_path = site_dir / 'data' / 'products' / f'{product_id}.json'
        if not full_path.exists():
            # 아직 수집 이력이 없는 제품 — 인라인도 비어 있어야 한다.
            assert inline == []
            continue
        full = json.loads(full_path.read_text(encoding='utf-8'))
        # 인라인은 전체 히스토리의 꼬리 구간이어야 폴백이 성립한다.
        assert inline == full[-INLINE_HISTORY_POINTS:]
        checked += 1
        if checked >= 20:
            break
    assert checked, '검사한 제품 페이지가 없다'
