"""정적 사이트 빌드 CLI — 출력 정리, 에셋 복사, 페이지 생성, 루트 publish."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from nikon_value.paths import PROJECT_ROOT
from nikon_value.sitegen.data import load_catalog, load_catalog_config, load_history, merge_catalog_with_config
from nikon_value.sitegen.pages import (
    build_404_page,
    build_home_page,
    build_product_page,
    build_resources_page,
    build_robots,
    build_sitemap,
)

STYLE_PATH = PROJECT_ROOT / 'css' / 'style.css'
SITE_JS_PATH = PROJECT_ROOT / 'js' / 'site.js'
AUTH_JS_PATH = PROJECT_ROOT / 'js' / 'auth.js'
HERO_JPG = PROJECT_ROOT / 'mynikons.jpg'
HERO_WEBP_800 = PROJECT_ROOT / 'assets' / 'mynikons-800.webp'
HERO_WEBP_1600 = PROJECT_ROOT / 'assets' / 'mynikons-1600.webp'
FILM_HISTORY_JPG_1 = PROJECT_ROOT / 'assets' / 'Nikon-camera-history1.jpg'
FILM_HISTORY_JPG_2 = PROJECT_ROOT / 'assets' / 'Nikon-camera-history2.jpg'
EBAY_LOGO = PROJECT_ROOT / 'assets' / 'ebay-logo.svg'
DEFAULT_OUTPUT = PROJECT_ROOT / 'dist'
ROOT_PRODUCTS_DIR = PROJECT_ROOT / 'products'
ROOT_FILES_TO_PUBLISH = [
    'index.html',
    'resources.html',
    '404.html',
    'robots.txt',
    'sitemap.xml',
    '.nojekyll',
]
LEGACY_ROOT_FILES_TO_REMOVE = ['board.html']


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default=str(DEFAULT_OUTPUT))
    parser.add_argument('--base-url', default='')
    parser.add_argument('--publish-root', action='store_true')
    return parser.parse_args()


def detect_base_url(cli_value: str) -> str:
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
            cwd=PROJECT_ROOT,
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


def copy_assets(output_dir: Path) -> None:
    ensure_dir(output_dir / 'css')
    ensure_dir(output_dir / 'js')
    ensure_dir(output_dir / 'assets')
    ensure_dir(output_dir / 'products')

    shutil.copy2(STYLE_PATH, output_dir / 'css' / 'style.css')
    shutil.copy2(SITE_JS_PATH, output_dir / 'js' / 'site.js')
    shutil.copy2(AUTH_JS_PATH, output_dir / 'js' / 'auth.js')
    shutil.copy2(EBAY_LOGO, output_dir / 'assets' / 'ebay-logo.svg')
    shutil.copy2(HERO_WEBP_800, output_dir / 'assets' / 'mynikons-800.webp')
    shutil.copy2(HERO_WEBP_1600, output_dir / 'assets' / 'mynikons-1600.webp')
    shutil.copy2(FILM_HISTORY_JPG_1, output_dir / 'assets' / 'Nikon-camera-history1.jpg')
    shutil.copy2(FILM_HISTORY_JPG_2, output_dir / 'assets' / 'Nikon-camera-history2.jpg')
    shutil.copy2(HERO_JPG, output_dir / 'mynikons.jpg')
    (output_dir / '.nojekyll').write_text('', encoding='utf-8')


def publish_root_site(output_dir: Path) -> None:
    for name in ROOT_FILES_TO_PUBLISH:
        source = output_dir / name
        if source.exists():
            shutil.copy2(source, PROJECT_ROOT / name)

    for name in LEGACY_ROOT_FILES_TO_REMOVE:
        target = PROJECT_ROOT / name
        if target.exists():
            target.unlink()

    if ROOT_PRODUCTS_DIR.exists():
        shutil.rmtree(ROOT_PRODUCTS_DIR)
    shutil.copytree(output_dir / 'products', ROOT_PRODUCTS_DIR)


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output).resolve()
    base_url = detect_base_url(args.base_url)
    catalog = merge_catalog_with_config(load_catalog(), load_catalog_config())
    histories = {
        product['id']: load_history(product['id'])
        for category in catalog['categories']
        for product in category['products']
    }

    clean_output(output_dir)
    copy_assets(output_dir)

    (output_dir / 'index.html').write_text(build_home_page(catalog, base_url, histories), encoding='utf-8')
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
        publish_root_site(output_dir)
