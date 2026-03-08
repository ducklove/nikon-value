#!/usr/bin/env python3
"""Build the static GitHub Pages artifact for the Nikon value tracker."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / 'data'
CATALOG_PATH = DATA_DIR / 'catalog.json'
CONFIG_PATH = PROJECT_ROOT / 'config' / 'products.yaml'
STYLE_PATH = PROJECT_ROOT / 'css' / 'style.css'
SITE_JS_PATH = PROJECT_ROOT / 'js' / 'site.js'
HERO_JPG = PROJECT_ROOT / 'mynikons.jpg'
HERO_WEBP_800 = PROJECT_ROOT / 'assets' / 'mynikons-800.webp'
HERO_WEBP_1600 = PROJECT_ROOT / 'assets' / 'mynikons-1600.webp'
EBAY_LOGO = PROJECT_ROOT / 'assets' / 'ebay-logo.svg'
DEFAULT_OUTPUT = PROJECT_ROOT / 'dist'
BODY_CATEGORIES = {'z-mount-bodies', 'f-mount-dslr', 'film-cameras'}
GA_MEASUREMENT_ID = 'G-823D75RRWJ'
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


def load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding='utf-8'))


def load_catalog_config() -> dict[str, Any]:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding='utf-8'))


def load_history(product_id: str) -> list[dict[str, Any]]:
    history_path = DATA_DIR / 'products' / f'{product_id}.json'
    if not history_path.exists():
        return []
    return json.loads(history_path.read_text(encoding='utf-8'))


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


def is_lens_category(category_id: str) -> bool:
    return category_id.endswith('-lenses')


def sort_products(products: list[dict[str, Any]], category_id: str) -> list[dict[str, Any]]:
    items = list(products)
    if category_id in BODY_CATEGORIES:
        items.sort(key=lambda item: item.get('release_year') or 0, reverse=True)
    elif is_lens_category(category_id):
        items.sort(key=lambda item: item.get('focal_length_min') or 0)
    return items


def format_money(value: Any) -> str:
    if value is None:
        return '-'
    amount = float(value)
    if amount.is_integer():
        return f'${int(amount):,}'
    return f'${amount:,.2f}'


def render_money_span(value: Any, *, extra_class: str = '', sign: str = 'auto') -> str:
    classes = 'money-value'
    if extra_class:
        classes = f'{classes} {extra_class}'
    if value is None:
        return f'<span class="{classes}">{escape(format_money(value))}</span>'
    amount = float(value)
    return (
        f'<span class="{classes}" data-money-usd="{escape(str(amount))}"'
        f' data-money-sign="{escape(sign)}">{escape(format_money(amount))}</span>'
    )


def render_money_range(start: Any, end: Any, *, extra_class: str = '') -> str:
    classes = 'money-range'
    if extra_class:
        classes = f'{classes} {extra_class}'
    if start is None or end is None:
        return f'<span class="{classes}">-</span>'
    return (
        f'<span class="{classes}">'
        f'{render_money_span(start, extra_class=extra_class)}'
        ' - '
        f'{render_money_span(end, extra_class=extra_class)}'
        '</span>'
    )


def format_exchange_rate(exchange_rate: dict[str, Any] | None) -> str:
    if not exchange_rate or exchange_rate.get('rate') is None:
        return 'KRW 환산용 환율 데이터를 불러오지 못했습니다.'
    rate = float(exchange_rate['rate'])
    reference_date = exchange_rate.get('reference_date') or '-'
    source = exchange_rate.get('source') or '환율 데이터'
    return f'USD 1 = KRW {rate:,.2f} ({source} {reference_date} 기준)'


def format_exchange_rate_inline(exchange_rate: dict[str, Any] | None) -> str:
    if not exchange_rate or exchange_rate.get('rate') is None:
        return ''
    return f' (USD/KRW = {float(exchange_rate["rate"]):,.2f})'


def build_currency_toggle(exchange_rate: dict[str, Any] | None, *, compact: bool = False) -> str:
    disabled = '' if exchange_rate and exchange_rate.get('rate') else ' disabled'
    compact_class = ' currency-toggle-panel--compact' if compact else ''
    return f"""
        <div class=\"currency-toggle-panel{compact_class}\">
          <div class=\"currency-toggle\" role=\"group\" aria-label=\"표시 통화 선택\">
            <button class=\"currency-toggle__button\" type=\"button\" data-currency=\"usd\">USD</button>
            <button class=\"currency-toggle__button\" type=\"button\" data-currency=\"krw\"{disabled}>KRW</button>
          </div>
        </div>"""


def json_script(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False).replace('</script>', '<\\/script>')


def merge_catalog_with_config(live_catalog: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    metric_defaults = {
        'median': None,
        'mean': None,
        'min': None,
        'max': None,
        'q1': None,
        'q3': None,
        'count': 0,
        'count_filtered': 0,
        'samples': [],
    }
    live_categories = {category['id']: category for category in live_catalog.get('categories', [])}
    merged_categories = []

    for config_category in config.get('categories', []):
        live_category = live_categories.get(config_category['id'], {})
        live_products = {
            product['id']: product
            for product in live_category.get('products', [])
        }
        merged_products = []

        for config_product in config_category.get('products', []):
            live_product = live_products.get(config_product['id'], {})
            merged_product = dict(metric_defaults)
            merged_product.update(live_product)
            merged_product.update(
                {
                    key: value
                    for key, value in config_product.items()
                    if key not in {'query', 'category_id', 'search_category_id', 'min_price', 'max_price'}
                }
            )
            merged_product['samples'] = live_product.get('samples', [])
            merged_products.append(merged_product)

        merged_categories.append(
            {
                'id': config_category['id'],
                'name_ko': config_category['name_ko'],
                'name_en': config_category['name_en'],
                'subcategories': config_category.get('subcategories', []),
                'products': merged_products,
            }
        )

    return {
        'updated': live_catalog.get('updated', date.today().isoformat()),
        'exchange_rate': live_catalog.get('exchange_rate'),
        'categories': merged_categories,
    }


def ga_snippet() -> str:
    return f"""  <script async src=\"https://www.googletagmanager.com/gtag/js?id={GA_MEASUREMENT_ID}\"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){{dataLayer.push(arguments);}}
    gtag('js', new Date());
    gtag('config', '{GA_MEASUREMENT_ID}');
  </script>"""


def build_hero_manual_hotspots() -> str:
    hotspots = [
        ('Zf', 'https://onlinemanual.nikonimglib.com/zf/ko/', 'left: 19.5%; top: 23%; width: 12%; height: 14%;'),
        ('F3', 'https://cdn-10.nikon-cdn.com/pdf/manuals/archive/F3.pdf', 'left: 48%; top: 31%; width: 10%; height: 14%;'),
        ('Nikkormat', 'https://www.cameramanuals.org/nikon_pdf/nikkormat_ftn.pdf', 'left: 62.5%; top: 18%; width: 11%; height: 12%;'),
        ('FM2', 'https://cdn-10.nikon-cdn.com/pdf/manuals/archive/FM2.pdf', 'left: 87.5%; top: 40%; width: 10%; height: 14%;'),
    ]
    links = []
    for label, href, position in hotspots:
        links.append(
            f'<a class="hero-hotspot" href="{escape(href)}" target="_blank" rel="noopener noreferrer" style="{position}">'
            f'<span class="visually-hidden">{escape(label)} manual</span>'
            '</a>'
        )
    return (
        '<div class="hero-hotspots" aria-label="히어로 이미지 카메라 메뉴얼 바로가기">'
        f'{"".join(links)}'
        '</div>'
    )


def head_block(*, title: str, description: str, canonical: str, image_url: str, extra_meta: str = '') -> str:
    canonical_tag = f'  <link rel="canonical" href="{escape(canonical)}">\n' if canonical else ''
    og_url = f'  <meta property="og:url" content="{escape(canonical)}">\n' if canonical else ''
    return f"""<head>
  <meta charset=\"UTF-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
  <title>{escape(title)}</title>
  <meta name=\"description\" content=\"{escape(description)}\">
  <meta property=\"og:type\" content=\"website\">
  <meta property=\"og:title\" content=\"{escape(title)}\">
  <meta property=\"og:description\" content=\"{escape(description)}\">
  <meta property=\"og:image\" content=\"{escape(image_url)}\">
{og_url}{canonical_tag}{ga_snippet()}
  <link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">
  <link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>
  <link href=\"https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap\" rel=\"stylesheet\">
  <link rel=\"stylesheet\" href=\"css/style.css\">
{extra_meta}</head>"""


def head_block_product(*, title: str, description: str, canonical: str, image_url: str, extra_meta: str = '') -> str:
    canonical_tag = f'  <link rel="canonical" href="{escape(canonical)}">\n' if canonical else ''
    og_url = f'  <meta property="og:url" content="{escape(canonical)}">\n' if canonical else ''
    return f"""<head>
  <meta charset=\"UTF-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
  <title>{escape(title)}</title>
  <meta name=\"description\" content=\"{escape(description)}\">
  <meta property=\"og:type\" content=\"article\">
  <meta property=\"og:title\" content=\"{escape(title)}\">
  <meta property=\"og:description\" content=\"{escape(description)}\">
  <meta property=\"og:image\" content=\"{escape(image_url)}\">
{og_url}{canonical_tag}{ga_snippet()}
  <link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">
  <link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>
  <link href=\"https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap\" rel=\"stylesheet\">
  <link rel=\"stylesheet\" href=\"../css/style.css\">
{extra_meta}</head>"""


def build_site_links(active: str, prefix: str = '') -> str:
    links = [
        ('home', f'{prefix}index.html', '시세 목록'),
        ('resources', f'{prefix}resources.html', '참고 링크'),
    ]
    items = []
    for key, href, label in links:
        class_name = 'site-link is-active' if key == active else 'site-link'
        items.append(f'<a class="{class_name}" href="{escape(href)}">{escape(label)}</a>')
    return (
        '<div class="site-links-wrap">'
        '<div class="container">'
        '<nav class="site-links" aria-label="사이트 바로가기">'
        f'{"".join(items)}'
        '</nav>'
        '</div>'
        '</div>'
    )


def build_footer(asset_prefix: str = '') -> str:
    return f"""
  <footer class=\"site-footer\">
    <div class=\"container\">
      <div class=\"footer-attribution\">
        <img src=\"{escape(asset_prefix)}assets/ebay-logo.svg\" alt=\"eBay\" class=\"ebay-logo\">
        <span>Powered by eBay Browse API</span>
      </div>
      <p class=\"footer-note\">가격은 현재 eBay 매물 기준이며, 실제 거래가와 다를 수 있습니다.</p>
    </div>
  </footer>"""


def compute_stale_days(updated: str) -> int:
    updated_date = date.fromisoformat(updated)
    return (datetime.now().date() - updated_date).days


def compute_price_change(history: list[dict[str, Any]], days: int) -> dict[str, Any] | None:
    valid = [entry for entry in history if entry.get('median') is not None]
    if len(valid) < 2:
        return None

    latest = valid[-1]
    latest_date = date.fromisoformat(latest['date'])
    cutoff = latest_date - timedelta(days=days)
    baseline = None
    for entry in reversed(valid[:-1]):
        if date.fromisoformat(entry['date']) <= cutoff:
            baseline = entry
            break

    if baseline is None:
        baseline = valid[0]
        if baseline['date'] == latest['date']:
            return None

    baseline_price = float(baseline['median'])
    latest_price = float(latest['median'])
    if baseline_price <= 0:
        return None

    delta_value = latest_price - baseline_price
    delta_pct = (delta_value / baseline_price) * 100
    baseline_date = date.fromisoformat(baseline['date'])
    return {
        'days': days,
        'baseline_date': baseline['date'],
        'baseline_median': baseline_price,
        'latest_date': latest['date'],
        'latest_median': latest_price,
        'delta_value': delta_value,
        'delta_pct': delta_pct,
        'actual_days': (latest_date - baseline_date).days,
    }


def format_change_percent(change: dict[str, Any] | None) -> str:
    if not change:
        return '-'
    value = change['delta_pct']
    sign = '+' if value > 0 else ''
    return f'{sign}{value:.1f}%'


def format_change_value(change: dict[str, Any] | None) -> str:
    if not change:
        return '-'
    value = change['delta_value']
    sign = '+' if value > 0 else ''
    return f'{sign}{format_money(value)}'


def has_catalog_listing_data(product: dict[str, Any]) -> bool:
    return (product.get('count') or 0) > 0


def build_home_page(catalog: dict[str, Any], base_url: str) -> str:
    updated = catalog['updated']
    exchange_rate = catalog.get('exchange_rate')
    stale_days = compute_stale_days(updated)
    total_products = sum(
        1
        for category in catalog['categories']
        for product in category['products']
        if has_catalog_listing_data(product)
    )
    total_listings = sum(
        (product.get('count') or 0)
        for category in catalog['categories']
        for product in category['products']
        if has_catalog_listing_data(product)
    )
    total_categories = len(catalog['categories'])
    rare_live_products = []
    stale_banner = ''
    if stale_days >= 2:
        stale_banner = f"""
      <div class=\"stale-banner\" role=\"status\">
        <strong>데이터 점검 필요</strong>
        <span>마지막 업데이트가 {stale_days}일 전({escape(updated)})입니다. 자동 수집 워크플로 상태를 확인하세요.</span>
      </div>"""

    tabs = ['<button class="category-tab active" type="button" data-category-id="all">전체</button>']
    feature_order = 0
    cards = []
    rare_watch_cards = []
    image_url = f'{base_url}/assets/mynikons-1600.webp' if base_url else 'assets/mynikons-1600.webp'

    for category in catalog['categories']:
        tabs.append(
            f'<button class="category-tab" type="button" data-category-id="{escape(category["id"])}">{escape(category["name_ko"])}</button>'
        )
        subcategory_lookup = {
            item['id']: item['name_ko']
            for item in category.get('subcategories', [])
        }
        for product in sort_products(category['products'], category['id']):
            if not has_catalog_listing_data(product):
                continue
            feature_order += 1
            thumb = ''
            samples = product.get('samples') or []
            if samples and samples[0].get('image'):
                thumb = (
                    f'<img class="product-card__thumb" src="{escape(samples[0]["image"])}" '
                    f'alt="{escape(product["name_en"])}" loading="lazy">'
                )
            else:
                thumb = '<div class="product-card__thumb-placeholder" aria-hidden="true">Nikon</div>'

            category_label = category['name_ko']
            if product.get('subcategory') and subcategory_lookup.get(product['subcategory']):
                category_label = f"{category_label} / {subcategory_lookup[product['subcategory']]}"
            price_html = (
                f'<div class="product-card__price">{render_money_span(product.get("median"))}</div>'
                if product.get('median') is not None
                else '<div class="product-card__price product-card__price--na">데이터 없음</div>'
            )
            range_html = ''
            if product.get('q1') is not None and product.get('q3') is not None:
                range_html = (
                    f'<div class="product-card__range">Q1-Q3: {render_money_range(product["q1"], product["q3"])}</div>'
                )
            badge_value = product.get('release_year') or (
                f'{product["focal_length_min"]}mm' if product.get('focal_length_min') else ''
            )
            badges = []
            if badge_value:
                badges.append(f'<span class="product-card__badge">{escape(str(badge_value))}</span>')
            if product.get('is_rare'):
                rarity_label = f"희귀 {product.get('rarity_tier') or ''}".strip()
                badges.append(f'<span class="product-card__badge product-card__badge--rare">{escape(rarity_label)}</span>')
            badge_html = f'<div class="product-card__badges">{"".join(badges)}</div>' if badges else ''
            priority_value = product.get('release_year') or product.get('focal_length_min') or 0
            search_index = ' '.join(
                filter(
                    None,
                    [
                        product['id'],
                        product['name_ko'],
                        product['name_en'],
                        category['name_ko'],
                        category['name_en'],
                        subcategory_lookup.get(product.get('subcategory') or '', ''),
                        product.get('rarity_tier', ''),
                        product.get('rarity_note', ''),
                    ],
                )
            ).lower()
            if product.get('is_rare') and (product.get('count') or 0) > 0:
                rare_live_products.append(
                    {
                        'product': product,
                        'category_id': category['id'],
                        'category_label': category_label,
                    }
                )
            cards.append(
                f"""
      <a class=\"product-card\" href=\"products/{escape(product['id'])}.html\"
         data-product-id=\"{escape(product['id'])}\"
         data-category-id=\"{escape(category['id'])}\"
         data-search=\"{escape(search_index)}\"
         data-name-ko=\"{escape(product['name_ko'])}\"
         data-median=\"{'' if product.get('median') is None else escape(str(product['median']))}\"
         data-count=\"{escape(str(product.get('count') or 0))}\"
         data-release-year=\"{escape(str(product.get('release_year') or 0))}\"
         data-priority=\"{escape(str(priority_value))}\"
         data-feature-order=\"{feature_order}\">
        {thumb}
        <div class=\"product-card__body\">
          <div class=\"product-card__header\">
            <div class=\"product-card__name\">{escape(product['name_ko'])}</div>
            {badge_html}
          </div>
          <div class=\"product-card__name-en\">{escape(product['name_en'])}</div>
          <div class=\"product-card__taxonomy\">{escape(category_label)}</div>
          {price_html}
          <div class=\"product-card__meta\"><span>현재 매물 {escape(str(product.get('count') or 0))}개</span></div>
          {range_html}
        </div>
      </a>"""
            )

    description = (
        f'eBay 미국 현재 매물 기준으로 니콘 카메라와 렌즈 {total_products}개 모델의 중고 시세를 추적합니다. '
        f'마지막 업데이트 {updated}.'
    )
    canonical = f'{base_url}/' if base_url else ''
    schema = {
        '@context': 'https://schema.org',
        '@type': 'CollectionPage',
        'name': '니콘 중고 시세 트래커',
        'description': description,
        'dateModified': updated,
    }
    extra_meta = f"  <script type=\"application/ld+json\">{json_script(schema)}</script>\n"
    shortcut_cards = ""
    rare_live_products.sort(
        key=lambda item: (
            -(item['product'].get('rarity_sort') or 0),
            -(item['product'].get('median') or 0),
            item['product'].get('count') or 0,
            item['product']['name_ko'],
        )
    )
    for item in rare_live_products:
        product = item['product']
        rare_watch_cards.append(
            f"""
      <a class=\"rare-watch-card\" href=\"products/{escape(product['id'])}.html\"
         data-category-id=\"{escape(item['category_id'])}\"
         data-search=\"{escape(' '.join(filter(None, [product['id'], product['name_ko'], product['name_en'], item['category_label'], product.get('rarity_tier', ''), product.get('rarity_note', '')])).lower())}\">
        <div class=\"rare-watch-card__top\">
          <span class=\"rare-watch-card__tier\">{escape(product.get('rarity_tier') or '희귀')}</span>
          <span class=\"rare-watch-card__count\">현재 매물 {escape(str(product.get('count') or 0))}개</span>
        </div>
        <strong>{escape(product['name_ko'])}</strong>
        <div class=\"rare-watch-card__name-en\">{escape(product['name_en'])}</div>
        <div class=\"rare-watch-card__taxonomy\">{escape(item['category_label'])}</div>
        <div class=\"rare-watch-card__price\">현재 중앙값 {render_money_span(product.get('median'))}</div>
        <div class=\"rare-watch-card__hint\">최근 희귀 시세 {escape(product.get('rarity_price_hint') or '공개 표본 부족')}</div>
        <p class=\"rare-watch-card__note\">{escape(product.get('rarity_note') or '개별 상태 확인 필요')}</p>
      </a>"""
        )
    rare_watch_html = ''
    if rare_watch_cards:
        rare_watch_html = f"""
    <section id=\"rare-watch\" class=\"rare-watch\" aria-labelledby=\"rare-watch-title\">
      <div class=\"rare-watch__header\">
        <div>
          <span class=\"section-kicker\">Rare listing watch</span>
          <h2 id=\"rare-watch-title\" class=\"section-heading\">희귀 매물 감지</h2>
        </div>
        <p id=\"rare-watch-summary\" class=\"rare-watch__summary\">현재 {len(rare_watch_cards)}개 모델에서 희귀 매물이 감지되었습니다.</p>
      </div>
      <div class=\"rare-watch-grid\">
{''.join(rare_watch_cards)}
      </div>
    </section>"""

    return f"""<!DOCTYPE html>
<html lang=\"ko\">
{head_block(title='니콘 중고 시세 트래커', description=description, canonical=canonical, image_url=image_url, extra_meta=extra_meta)}
<body data-page=\"catalog\">
  <header class=\"site-header\">
    <div class=\"hero-banner\">
      <picture>
        <source type=\"image/webp\" srcset=\"assets/mynikons-800.webp 800w, assets/mynikons-1600.webp 1600w\" sizes=\"100vw\">
        <img src=\"mynikons.jpg\" alt=\"Nikon camera collection\" class=\"hero-image\" width=\"1600\" height=\"900\" fetchpriority=\"high\" loading=\"eager\" decoding=\"async\">
      </picture>
      {build_hero_manual_hotspots()}
      <div class=\"hero-overlay\">
        <div class=\"container\">
          <h1 class=\"site-title\">니콘 중고 시세 트래커</h1>
          <p class=\"site-subtitle\">eBay 현재 매물 기준 시세 (배송비 포함)</p>
          <p class=\"site-updated\">최종 업데이트: {escape(updated)}{escape(format_exchange_rate_inline(exchange_rate))}</p>
        </div>
      </div>
    </div>
  </header>
  {build_site_links('home')}

  <nav class=\"category-nav\" aria-label=\"카테고리 필터\">
    <div class=\"container\">
      <div class=\"category-tabs\">{''.join(tabs)}</div>
    </div>
  </nav>

  <main class=\"container\">
    <section class=\"catalog-toolbar\" aria-label=\"시세 탐색\">
      <div class=\"catalog-toolbar__copy\">
        <span class=\"section-kicker\">Nikon used value tracker</span>
        <h2 class=\"section-heading\">카테고리 <span id=\"catalog-context\">전체</span></h2>
        <div class=\"catalog-stats\">
          <span><strong id=\"visible-count\">{total_products}</strong>개 모델 표시 중</span>
          <span><strong>{total_categories}</strong>개 카테고리</span>
          <span><strong>{total_listings:,}</strong>개 현재 매물 추적</span>
        </div>
      </div>
      <div class=\"toolbar-controls\">
        {build_currency_toggle(exchange_rate)}
        <div class=\"toolbar-controls__row\">
          <label class=\"visually-hidden\" for=\"search-input\">제품 검색</label>
          <input class=\"search-input\" id=\"search-input\" type=\"search\" placeholder=\"제품명, 영문명, 카테고리 검색\">
          <label class=\"visually-hidden\" for=\"sort-select\">정렬</label>
          <select class=\"sort-select\" id=\"sort-select\">
            <option value=\"featured\">기본 정렬</option>
            <option value=\"price-asc\">중앙값 낮은 순</option>
            <option value=\"price-desc\">중앙값 높은 순</option>
            <option value=\"count-desc\">매물 많은 순</option>
            <option value=\"updated-desc\">최신 바디/긴 초점거리 우선</option>
            <option value=\"name-asc\">이름 순</option>
          </select>
        </div>
      </div>
    </section>{stale_banner}
{shortcut_cards}
{rare_watch_html}

    <div id=\"product-grid\" class=\"product-grid\">
{''.join(cards)}
    </div>
    <p id=\"catalog-empty\" class=\"empty-state-inline\" hidden>조건에 맞는 제품이 없습니다.</p>
  </main>
{build_footer()}

  <script id=\"exchange-rate-data\" type=\"application/json\">{json_script(exchange_rate or {})}</script>
  <script src=\"js/site.js\" defer></script>
</body>
</html>
"""


def product_image(product: dict[str, Any], base_url: str) -> str:
    samples = product.get('samples') or []
    if samples and samples[0].get('image'):
        return samples[0]['image']
    if base_url:
        return f'{base_url}/assets/mynikons-1600.webp'
    return '../assets/mynikons-1600.webp'


def render_product_offer_schema(product: dict[str, Any]) -> dict[str, Any]:
    schema: dict[str, Any] = {
        '@context': 'https://schema.org',
        '@type': 'Product',
        'brand': {'@type': 'Brand', 'name': 'Nikon'},
        'name': product['name_en'],
    }
    if product.get('count'):
        schema['offers'] = {
            '@type': 'AggregateOffer',
            'priceCurrency': 'USD',
            'offerCount': product['count'],
            'lowPrice': product.get('min'),
            'highPrice': product.get('max'),
        }
    return schema


def build_product_reference_cards(product: dict[str, Any], category: dict[str, Any], asset_prefix: str = '../') -> str:
    cards = [
        (
            f'{asset_prefix}resources.html',
            '사이트 참고 링크 모음',
            '이 제품군을 볼 때 함께 참고할 외부 자료를 한 페이지에 모아 두었습니다.',
            False,
        ),
        (
            'https://www.nikonmuseum.com/',
            'Nikon Museum',
            f'{product["name_ko"]}가 속한 니콘 시스템의 역사와 아카이브를 확인할 수 있습니다.',
            True,
        ),
    ]

    if is_lens_category(category['id']):
        cards.insert(
            1,
            (
                'http://www.photosynthesis.co.nz/nikon/lenses.html',
                'Photosynthesis Lens Database',
                '수동 렌즈 포함 Nikkor 계보와 변형 확인에 가장 실용적인 데이터베이스입니다.',
                True,
            ),
        )
        cards.insert(
            2,
            (
                'https://www.kenrockwell.com/nikon/nikkor.htm',
                'Ken Rockwell Nikon Lens Index',
                '렌즈 세대별 특징과 실사용 맥락을 빠르게 훑어보기 좋습니다.',
                True,
            ),
        )

    rendered = []
    for href, title, description, external in cards:
        target = ' target="_blank" rel="noopener noreferrer"' if external else ''
        rendered.append(
            f"""
        <a class=\"detail-link-card\" href=\"{escape(href)}\"{target}>
          <strong>{escape(title)}</strong>
          <p>{escape(description)}</p>
        </a>"""
        )
    return ''.join(rendered)


def build_product_page(
    product: dict[str, Any],
    category: dict[str, Any],
    updated: str,
    history: list[dict[str, Any]],
    exchange_rate: dict[str, Any] | None,
    base_url: str,
) -> str:
    description = (
        f"{product['name_ko']} eBay 현재 매물 기준 중고 시세. "
        f"중앙값 {format_money(product.get('median'))}, 현재 매물 {product.get('count') or 0}개, 마지막 업데이트 {updated}."
    )
    canonical = f"{base_url}/products/{product['id']}.html" if base_url else ''
    image_url = product_image(product, base_url)
    recent_change = compute_price_change(history, 30)
    schema = render_product_offer_schema(product)
    extra_meta = f"  <script type=\"application/ld+json\">{json_script(schema)}</script>\n"
    subcategory_lookup = {
        item['id']: item['name_ko']
        for item in category.get('subcategories', [])
    }
    breadcrumb = category['name_ko']
    if product.get('subcategory') and product['subcategory'] in subcategory_lookup:
        breadcrumb = f"{breadcrumb} / {subcategory_lookup[product['subcategory']]}"

    summary_cards = [
        ('중앙값', render_money_span(product.get('median')), 'price-card price-card--primary'),
        ('평균', render_money_span(product.get('mean')), 'price-card'),
        ('최저', render_money_span(product.get('min')), 'price-card'),
        ('최고', render_money_span(product.get('max')), 'price-card'),
        ('매물 수', escape(str(product.get('count') or 0)), 'price-card'),
        ('Q1 - Q3', render_money_range(product.get('q1'), product.get('q3'), extra_class='price-value--small'), 'price-card'),
        ('최근 변화', escape(format_change_percent(recent_change)), 'price-card'),
        ('변화 금액', render_money_span(recent_change['delta_value'], sign='always') if recent_change else '-', 'price-card'),
    ]
    summary_html = '\n'.join(
        f"""        <div class=\"{klass}\">\n          <span class=\"price-label\">{escape(label)}</span>\n          <span class=\"price-value{' price-value--small' if 'Q1' in label else ''}\">{value}</span>\n        </div>"""
        for label, value, klass in summary_cards
    )

    listing_cards = []
    for sample in product.get('samples') or []:
        image = sample.get('image') or ''
        image_tag = (
            f'<img class="listing-card__image" src="{escape(image)}" alt="{escape(sample.get("title", ""))}" loading="lazy">'
            if image
            else ''
        )
        listing_cards.append(
            f"""
        <a class=\"listing-card\" href=\"{escape(sample.get('url', '#'))}\" target=\"_blank\" rel=\"noopener noreferrer nofollow\">
          {image_tag}
          <div class=\"listing-card__info\">
            <div class=\"listing-card__title\">{escape(sample.get('title', ''))}</div>
            <div class=\"listing-card__price\">{render_money_span(sample.get('price'))}</div>
          </div>
        </a>"""
        )
    if not listing_cards:
        listing_cards.append('<p class="detail-note">현재 노출할 샘플 매물이 없습니다.</p>')

    history_rows = []
    for entry in reversed(history[-10:]):
        history_rows.append(
            f"<tr><td>{escape(entry['date'])}</td><td>{escape(format_money(entry.get('median')))}</td><td>{escape(format_money(entry.get('q1')))} - {escape(format_money(entry.get('q3')))}</td><td>{escape(str(entry.get('count') or 0))}</td></tr>"
        )
    history_table = ''
    if history_rows:
        history_table = f"""
      <noscript>
        <table class=\"history-table\">
          <thead>
            <tr><th>날짜</th><th>중앙값</th><th>Q1-Q3</th><th>매물 수</th></tr>
          </thead>
          <tbody>{''.join(history_rows)}</tbody>
        </table>
      </noscript>"""

    meta_pills = [
        f'<span class="meta-pill">카테고리 {escape(breadcrumb)}</span>',
        f'<span class="meta-pill">업데이트 {escape(updated)}</span>',
        f'<span class="meta-pill">매물 {escape(str(product.get("count") or 0))}개</span>',
    ]
    if product.get('is_rare'):
        meta_pills.append(f'<span class="meta-pill">희귀 등급 {escape(product.get("rarity_tier") or "-")}</span>')
    reference_cards = build_product_reference_cards(product, category)
    movement_note = '최근 변화 데이터를 아직 만들기 어렵습니다.'
    if recent_change:
        movement_note = (
            f"최근 {recent_change['actual_days']}일 기준 중앙값 {format_change_percent(recent_change)} "
            f"({render_money_span(recent_change['delta_value'], sign='always')}) 변동했습니다."
        )
    rare_note_html = ''
    if product.get('is_rare'):
        rare_note_html = (
            f'<div class="rare-detail-note"><strong>희귀 모델 {escape(product.get("rarity_tier") or "-")}</strong>'
            f'<span>최근 희귀 시세 {escape(product.get("rarity_price_hint") or "공개 표본 부족")}. '
            f'{escape(product.get("rarity_note") or "개별 상태와 구성품에 따라 편차가 큽니다.")}</span></div>'
        )

    return f"""<!DOCTYPE html>
<html lang=\"ko\">
{head_block_product(title=f"{product['name_ko']} - 니콘 중고 시세", description=description, canonical=canonical, image_url=image_url, extra_meta=extra_meta)}
<body data-page=\"product\" data-default-period=\"180\">
  <header class=\"site-header\">
    <div class=\"container\">
      <a href=\"../index.html\" class=\"back-link\">&larr; 전체 목록</a>
      <h1 class=\"product-title\">{escape(product['name_ko'])}</h1>
      <p class=\"product-subtitle\">{escape(product['name_en'])}</p>
      <div class=\"product-header-meta\">{''.join(meta_pills)}</div>
    </div>
  </header>
  {build_site_links('home', '../')}

  <main class=\"container\">
    <div class=\"detail-toolbar\">
      {build_currency_toggle(exchange_rate, compact=True)}
    </div>
    <div class=\"price-summary\">
{summary_html}
    </div>
    {rare_note_html}

    <p class=\"detail-note\">시세는 eBay 미국 현재 매물 기준이며, 실제 체결가와는 차이가 있을 수 있습니다. {movement_note}</p>

    <section class=\"chart-section\">
      <div class=\"chart-header\">
        <h2>시세 추이</h2>
        <div class=\"period-selector\">
          <button class=\"period-btn\" type=\"button\" data-period=\"30\">1개월</button>
          <button class=\"period-btn\" type=\"button\" data-period=\"90\">3개월</button>
          <button class=\"period-btn active\" type=\"button\" data-period=\"180\">6개월</button>
          <button class=\"period-btn\" type=\"button\" data-period=\"365\">1년</button>
          <button class=\"period-btn\" type=\"button\" data-period=\"0\">전체</button>
        </div>
      </div>
      <div class=\"chart-container\">
        <canvas id=\"price-chart\"></canvas>
        <p class=\"chart-empty\" id=\"chart-empty\" hidden>표시할 시계열 데이터가 충분하지 않습니다.</p>
      </div>
{history_table}
    </section>

    <section class=\"listings-section\">
      <h2>현재 매물 예시</h2>
      <div class=\"listings-grid\">
{''.join(listing_cards)}
      </div>
    </section>

    <section class=\"references-section\">
      <div class=\"section-header-row\">
        <div>
          <span class=\"section-kicker\">Research links</span>
          <h2 class=\"section-heading\">참고 자료</h2>
        </div>
      </div>
      <div class=\"detail-links-grid\">
{reference_cards}
      </div>
    </section>
  </main>
{build_footer('../')}

  <script id=\"exchange-rate-data\" type=\"application/json\">{json_script(exchange_rate or {})}</script>
  <script id=\"history-data\" type=\"application/json\">{json_script(history)}</script>
  <script src=\"https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js\"></script>
  <script src=\"../js/site.js\" defer></script>
</body>
</html>
"""


def build_resources_page(base_url: str) -> str:
    canonical = f'{base_url}/resources.html' if base_url else ''
    image_url = f'{base_url}/assets/mynikons-1600.webp' if base_url else 'assets/mynikons-1600.webp'
    description = '니콘 렌즈 계보, 리뷰, 역사 자료를 볼 수 있는 참고 사이트 모음입니다.'
    schema = {
        '@context': 'https://schema.org',
        '@type': 'CollectionPage',
        'name': '참고 사이트 링크',
        'description': description,
    }
    extra_meta = f"  <script type=\"application/ld+json\">{json_script(schema)}</script>\n"
    resources = [
        (
            'Photosynthesis Nikon Lens Database',
            'http://www.photosynthesis.co.nz/nikon/lenses.html',
            '수동 렌즈 포함 Nikon/Nikkor 렌즈 계보와 세부 변형을 추적하기 좋습니다.',
        ),
        (
            'Ken Rockwell Nikon Lens Reviews',
            'https://www.kenrockwell.com/nikon/nikkor.htm',
            '렌즈별 실사용 리뷰와 세대별 특징을 빠르게 훑어보기 좋습니다.',
        ),
        (
            'Nikon Museum',
            'https://www.nikonmuseum.com/',
            '바디와 렌즈의 역사, 연표, 아카이브 자료를 확인할 수 있습니다.',
        ),
    ]
    cards = []
    for title, url, desc in resources:
        cards.append(
            f"""
      <a class=\"resource-card\" href=\"{escape(url)}\" target=\"_blank\" rel=\"noopener noreferrer\">
        <span class=\"resource-card__host\">{escape(url.split('//', 1)[1].split('/', 1)[0])}</span>
        <h2>{escape(title)}</h2>
        <p>{escape(desc)}</p>
      </a>"""
        )
    return f"""<!DOCTYPE html>
<html lang=\"ko\">
{head_block(title='참고 사이트 링크 - 니콘 중고 시세 트래커', description=description, canonical=canonical, image_url=image_url, extra_meta=extra_meta)}
<body data-page=\"resources\">
  <header class=\"site-header\">
    <div class=\"hero-banner\">
      <picture>
        <source type=\"image/webp\" srcset=\"assets/mynikons-800.webp 800w, assets/mynikons-1600.webp 1600w\" sizes=\"100vw\">
        <img src=\"mynikons.jpg\" alt=\"Nikon camera collection\" class=\"hero-image\" width=\"1600\" height=\"900\" fetchpriority=\"high\" loading=\"eager\" decoding=\"async\">
      </picture>
      {build_hero_manual_hotspots()}
      <div class=\"hero-overlay\">
        <div class=\"container\">
          <h1 class=\"site-title\">참고 사이트 링크</h1>
          <p class=\"site-subtitle\">시세 숫자 외에 계보, 사양, 역사 자료를 같이 볼 때 유용한 레퍼런스입니다.</p>
        </div>
      </div>
    </div>
  </header>
  {build_site_links('resources')}

  <main class=\"container page-main\">
    <section class=\"info-card info-card--wide\">
      <span class=\"section-kicker\">Reference library</span>
      <h2 class=\"section-heading\">니콘 자료실</h2>
      <p class=\"detail-note detail-note--normal\">렌즈 변형 확인, 세대 분류 검증, 제품 히스토리 파악에 자주 쓰는 링크만 우선 정리했습니다.</p>
    </section>

    <section class=\"resources-grid\" aria-label=\"외부 참고 링크\">
{''.join(cards)}
    </section>
  </main>

{build_footer()}
  <script src=\"js/site.js\" defer></script>
</body>
</html>
"""


def build_404_page(base_url: str) -> str:
    home_href = f'{base_url}/' if base_url else 'index.html'
    image_url = f'{base_url}/assets/mynikons-1600.webp' if base_url else 'assets/mynikons-1600.webp'
    return f"""<!DOCTYPE html>
<html lang=\"ko\">
{head_block(title='페이지를 찾을 수 없습니다', description='요청한 페이지를 찾을 수 없습니다.', canonical='', image_url=image_url)}
<body>
  <main class=\"container\" style=\"padding:80px 20px\">
    <div class=\"empty-state-inline\" style=\"max-width:560px;margin:0 auto\">
      <h1 class=\"section-heading\">페이지를 찾을 수 없습니다</h1>
      <p class=\"detail-note\">주소가 바뀌었거나 삭제된 페이지입니다.</p>
      <a class=\"category-tab active\" href=\"{escape(home_href)}\" style=\"display:inline-flex;text-decoration:none\">홈으로 돌아가기</a>
    </div>
  </main>
</body>
</html>
"""


def build_sitemap(catalog: dict[str, Any], base_url: str) -> str:
    if not base_url:
        return ''
    urls = [f'{base_url}/', f'{base_url}/resources.html']
    for category in catalog['categories']:
        for product in category['products']:
            urls.append(f"{base_url}/products/{product['id']}.html")
    entries = '\n'.join(
        f'  <url><loc>{escape(url)}</loc><lastmod>{escape(catalog["updated"])}</lastmod></url>'
        for url in urls
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'{entries}\n'
        '</urlset>\n'
    )


def build_robots(base_url: str) -> str:
    lines = ['User-agent: *', 'Allow: /']
    if base_url:
        lines.append(f'Sitemap: {base_url}/sitemap.xml')
    return '\n'.join(lines) + '\n'


def copy_assets(output_dir: Path) -> None:
    ensure_dir(output_dir / 'css')
    ensure_dir(output_dir / 'js')
    ensure_dir(output_dir / 'assets')
    ensure_dir(output_dir / 'products')

    shutil.copy2(STYLE_PATH, output_dir / 'css' / 'style.css')
    shutil.copy2(SITE_JS_PATH, output_dir / 'js' / 'site.js')
    shutil.copy2(EBAY_LOGO, output_dir / 'assets' / 'ebay-logo.svg')
    shutil.copy2(HERO_WEBP_800, output_dir / 'assets' / 'mynikons-800.webp')
    shutil.copy2(HERO_WEBP_1600, output_dir / 'assets' / 'mynikons-1600.webp')
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

    (output_dir / 'index.html').write_text(build_home_page(catalog, base_url), encoding='utf-8')
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


if __name__ == '__main__':
    main()
