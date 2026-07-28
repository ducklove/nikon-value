"""정적 사이트 빌드 CLI — 출력 정리, 에셋 복사, 페이지 생성, 루트 publish.

경로(저장소 루트·데이터 디렉터리)는 모듈 상수를 기본값으로 쓰되 인자로 주입할 수
있게 열어 두었다. 기본 인자만 쓰면 기존 동작과 100% 동일하다.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from nikon_value.paths import DATA_DIR, PROJECT_ROOT
from nikon_value.sitegen.data import load_catalog, load_catalog_config, load_history, merge_catalog_with_config
from nikon_value.sitegen.pages import (
    build_404_page,
    build_compare_page,
    build_home_page,
    build_product_page,
    build_resources_page,
    build_robots,
    build_sitemap,
)

# 산출물에 그대로 복사되는 정적 에셋: (저장소 루트 기준 소스, 산출물 기준 대상).
# shutil.copy2 호출을 하나씩 나열하면 새 에셋을 추가할 때 빠뜨리기 쉽고,
# 실제로 목록 누락 때문에 배포 사이트 이미지가 깨진 적이 있어 데이터로 관리한다.
ASSET_COPY_PLAN: tuple[tuple[str, str], ...] = (
    ('css/style.css', 'css/style.css'),
    ('js/site.js', 'js/site.js'),
    ('js/auth.js', 'js/auth.js'),
    # 비교 페이지(compare.html) 전용. 홈·제품 페이지는 로드하지 않는다.
    ('js/compare.js', 'js/compare.js'),
    ('assets/ebay-logo.svg', 'assets/ebay-logo.svg'),
    ('assets/mynikons-800.webp', 'assets/mynikons-800.webp'),
    ('assets/mynikons-1600.webp', 'assets/mynikons-1600.webp'),
    ('assets/Nikon-camera-history1.jpg', 'assets/Nikon-camera-history1.jpg'),
    ('assets/Nikon-camera-history2.jpg', 'assets/Nikon-camera-history2.jpg'),
    # 홈 히어로의 렌즈 카테고리 전용 이미지 — js/site.js의 updateHeroImage()가
    # z-mount-lenses/f-mount-lenses/classic-lenses 탭에서 노출한다.
    ('assets/nikon-lens-lineup.jpg', 'assets/nikon-lens-lineup.jpg'),
    ('mynikons.jpg', 'mynikons.jpg'),
    # Pages가 artifact만 서빙하므로, 과거에 저장소 루트에서 직접 서빙되던
    # OAuth 로그인 복귀 페이지도 산출물에 포함해야 한다.
    ('auth-complete.html', 'auth-complete.html'),
)

# 수집 데이터 중 산출물에 포함할 항목: (DATA_DIR 기준 소스, 산출물 기준 대상).
# 아직 수집 전이라 소스가 없을 수 있으므로 존재할 때만 복사한다.
# - catalog.json: API 서버의 제품 검증·가격 알림 체커가 읽는 URL
# - products/: 관심목록 가치 대시보드가 상대 경로로 fetch
DATA_COPY_PLAN: tuple[tuple[str, str], ...] = (
    ('catalog.json', 'data/catalog.json'),
    ('products', 'data/products'),
)

STYLE_PATH = PROJECT_ROOT / 'css' / 'style.css'
SITE_JS_PATH = PROJECT_ROOT / 'js' / 'site.js'
AUTH_JS_PATH = PROJECT_ROOT / 'js' / 'auth.js'
COMPARE_JS_PATH = PROJECT_ROOT / 'js' / 'compare.js'
HERO_JPG = PROJECT_ROOT / 'mynikons.jpg'
HERO_WEBP_800 = PROJECT_ROOT / 'assets' / 'mynikons-800.webp'
HERO_WEBP_1600 = PROJECT_ROOT / 'assets' / 'mynikons-1600.webp'
FILM_HISTORY_JPG_1 = PROJECT_ROOT / 'assets' / 'Nikon-camera-history1.jpg'
FILM_HISTORY_JPG_2 = PROJECT_ROOT / 'assets' / 'Nikon-camera-history2.jpg'
LENS_HERO_JPG = PROJECT_ROOT / 'assets' / 'nikon-lens-lineup.jpg'
EBAY_LOGO = PROJECT_ROOT / 'assets' / 'ebay-logo.svg'
DEFAULT_OUTPUT = PROJECT_ROOT / 'dist'
ROOT_PRODUCTS_DIR = PROJECT_ROOT / 'products'
ROOT_FILES_TO_PUBLISH = [
    'index.html',
    'compare.html',
    'resources.html',
    '404.html',
    'robots.txt',
    'sitemap.xml',
    '.nojekyll',
]
LEGACY_ROOT_FILES_TO_REMOVE = ['board.html']


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """CLI 인자를 파싱합니다. argv를 주면 sys.argv 대신 그것을 사용합니다."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default=str(DEFAULT_OUTPUT))
    parser.add_argument('--base-url', default='')
    parser.add_argument('--publish-root', action='store_true')
    return parser.parse_args(argv)


def detect_base_url(cli_value: str, *, project_root: Path = PROJECT_ROOT) -> str:
    """CLI 인자 → 환경변수 → git remote 순으로 사이트 base URL을 결정합니다."""
    if cli_value:
        return cli_value.rstrip('/')

    env_value = os.environ.get('SITE_BASE_URL')
    if env_value:
        return env_value.rstrip('/')

    repo_slug = os.environ.get('GITHUB_REPOSITORY')
    if repo_slug and '/' in repo_slug:
        owner, repo = repo_slug.split('/', 1)
        return f'https://{owner}.github.io/{repo}'

    try:
        remote = subprocess.check_output(
            ['git', 'config', '--get', 'remote.origin.url'],
            cwd=project_root,
            text=True,
        ).strip()
    except Exception:
        return ''

    remote = remote.removesuffix('.git')
    if remote.startswith('git@github.com:'):
        repo_slug = remote.split(':', 1)[1]
    elif remote.startswith('https://github.com/'):
        repo_slug = remote.split('https://github.com/', 1)[1]
    else:
        return ''

    if '/' not in repo_slug:
        return ''
    owner, repo = repo_slug.split('/', 1)
    return f'https://{owner}.github.io/{repo}'


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def clean_output(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    ensure_dir(path)


def copy_assets(
    output_dir: Path,
    *,
    project_root: Path = PROJECT_ROOT,
    data_dir: Path = DATA_DIR,
) -> None:
    """정적 에셋과 공개 데이터 파일을 산출물 디렉터리로 복사합니다."""
    ensure_dir(output_dir / 'products')
    ensure_dir(output_dir / 'data')

    for source_rel, target_rel in ASSET_COPY_PLAN:
        target = output_dir / target_rel
        ensure_dir(target.parent)
        shutil.copy2(project_root / source_rel, target)

    (output_dir / '.nojekyll').write_text('', encoding='utf-8')

    for source_rel, target_rel in DATA_COPY_PLAN:
        source = data_dir / source_rel
        if not source.exists():
            continue
        target = output_dir / target_rel
        ensure_dir(target.parent)
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)


def publish_root_site(output_dir: Path, *, project_root: Path = PROJECT_ROOT) -> None:
    """산출물을 저장소 루트로 복사해 GitHub Pages 루트 서빙과 동기화합니다."""
    for name in ROOT_FILES_TO_PUBLISH:
        source = output_dir / name
        if source.exists():
            shutil.copy2(source, project_root / name)

    for name in LEGACY_ROOT_FILES_TO_REMOVE:
        target = project_root / name
        if target.exists():
            target.unlink()

    root_products_dir = project_root / 'products'
    if root_products_dir.exists():
        shutil.rmtree(root_products_dir)
    shutil.copytree(output_dir / 'products', root_products_dir)


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path = PROJECT_ROOT,
    data_dir: Path = DATA_DIR,
) -> None:
    args = parse_args(argv)
    output_dir = Path(args.output).resolve()
    base_url = detect_base_url(args.base_url, project_root=project_root)
    catalog = merge_catalog_with_config(load_catalog(), load_catalog_config())
    histories = {
        product['id']: load_history(product['id'])
        for category in catalog['categories']
        for product in category['products']
    }

    clean_output(output_dir)
    copy_assets(output_dir, project_root=project_root, data_dir=data_dir)

    (output_dir / 'index.html').write_text(build_home_page(catalog, base_url, histories), encoding='utf-8')
    (output_dir / 'compare.html').write_text(build_compare_page(catalog, base_url), encoding='utf-8')
    (output_dir / 'resources.html').write_text(build_resources_page(base_url), encoding='utf-8')
    (output_dir / '404.html').write_text(build_404_page(base_url), encoding='utf-8')
    (output_dir / 'robots.txt').write_text(build_robots(base_url), encoding='utf-8')

    sitemap = build_sitemap(catalog, base_url)
    if sitemap:
        (output_dir / 'sitemap.xml').write_text(sitemap, encoding='utf-8')

    for category in catalog['categories']:
        for product in category['products']:
            history = histories[product['id']]
            product_html = build_product_page(
                product,
                category,
                catalog['updated'],
                history,
                catalog.get('exchange_rate'),
                base_url,
            )
            (output_dir / 'products' / f"{product['id']}.html").write_text(product_html, encoding='utf-8')

    if args.publish_root:
        publish_root_site(output_dir, project_root=project_root)
