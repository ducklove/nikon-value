"""페이지 공통 HTML 조각(헤드, 푸터, 비주얼 인덱스 등) 빌더."""

from __future__ import annotations

import os
import re
from html import escape
from typing import Any

from nikon_value.sitegen.data import is_lens_category
from nikon_value.sitegen.format import render_money_span

GA_MEASUREMENT_ID = 'G-823D75RRWJ'
# js/auth.js가 meta[name="nikon-api-base"]를 읽어 API 서버 주소를 결정한다.
DEFAULT_API_BASE_URL = 'https://cantabile.tplinkdns.com'

FILM_VISUAL_INDEX = [
    {
        'image': 'assets/Nikon-camera-history1.jpg',
        'alt': 'Nikon film camera history board part 1',
        'summary': '레인지파인더 & 초기 SLR (1948~1971)',
        'rows': 5,
        'cols': 5,
        'hotspots': [
            ('nikon-1', 0, 0),
            ('nikon-m', 0, 1),
            ('nikon-s', 0, 2),
            ('nikon-s2', 0, 3),
            ('nikon-sp', 0, 4),
            ('nikon-s3', 1, 0),
            ('nikon-s4', 1, 1),
            ('nikon-f', 1, 2),
            ('nikon-s3m', 1, 3),
            ('nikkorex-35', 1, 4),
            ('nikon-fisheye-camera', 2, 0),
            ('nikon-f-photomic', 2, 1),
            ('nikkorex-35ii', 2, 2),
            ('nikkorex-f', 2, 3),
            ('nikkorex-zoom-35', 2, 4),
            ('nikonos-i', 3, 0),
            ('nikkorex-auto-35', 3, 1),
            ('nikkormat-ft', 3, 2),
            ('nikon-f-photomic-t', 3, 3),
            ('nikkormat-fs', 3, 4),
            ('nikon-f-photomic-tn', 4, 0),
            ('nasa-spec-nikon-f', 4, 1),
            ('nikkormat-ftn', 4, 2),
            ('nikonos-ii', 4, 3),
            ('nikon-f2', 4, 4),
        ],
    },
    {
        'image': 'assets/Nikon-camera-history2.jpg',
        'alt': 'Nikon film camera history board part 2',
        'summary': 'SLR 전성기 & 초기 DSLR (1971~2005)',
        'rows': 25,
        'cols': 5,
        'image_height': 2772,
        'row_bounds': [
            0, 104, 217, 357, 504, 609, 745, 860, 956, 1052, 1152, 1253,
            1351, 1465, 1574, 1672, 1774, 1874, 1959, 2070, 2194, 2308, 2425, 2547, 2667, 2772,
        ],
        'hotspots': [
            ('nikkormat-el', 0, 1),
            ('nikonos-iii', 0, 4),
            ('nikkormat-ft3', 2, 0),
            ('nikon-fm', 2, 1),
            ('nikon-f2a-25th-anniversary', 2, 2),
            ('nikon-el2', 3, 0),
            ('nikon-f-high-speed', 3, 1),
            ('nikon-fe', 3, 2),
            ('nikon-em', 3, 3),
            ('nikon-f3', 3, 4),
            ('nikonos-iva', 4, 0),
            ('nikon-fg', 4, 1),
            ('nikon-fm2', 4, 2),
            ('nikon-f3hp', 4, 3),
            ('nikon-f3t', 4, 4),
            ('nikon-f3af', 5, 0),
            ('nikon-f3-limited', 5, 2),
            ('nikon-fa', 6, 0),
            ('nikon-fe2', 6, 1),
            ('nikon-fg20', 7, 0),
            ('nikonos-v', 7, 3),
            ('nikon-f4', 10, 0),
            ('nikonos-rs', 13, 1),
            ('nikon-35ti', 15, 2),
            ('nikon-f90x', 16, 0),
            ('nikon-28ti', 16, 3),
            ('nikon-fm10', 18, 0),
            ('nikon-fe10', 18, 3),
            ('nikon-f5', 18, 4),
            ('nikon-f100', 19, 3),
            ('nikon-f80', 20, 4),
            ('nikon-s3-2000-limited', 21, 2),
            ('nikon-fm3a', 22, 1),
            ('nikon-f6', 23, 3),
        ],
    },
]


def api_base_url() -> str:
    return os.environ.get('NIKON_API_BASE_URL', DEFAULT_API_BASE_URL).rstrip('/')


def ga_snippet() -> str:
    return f"""  <script async src=\"https://www.googletagmanager.com/gtag/js?id={GA_MEASUREMENT_ID}\"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){{dataLayer.push(arguments);}}
    gtag('js', new Date());
    gtag('config', '{GA_MEASUREMENT_ID}');
  </script>"""


def build_hero_manual_hotspots() -> str:
    zf_manual_href = 'https://onlinemanual.nikonimglib.com/zf/ko/'
    zf_manual_position = 'left: 19.592%; top: 33.537%; width: 8.306%; height: 11.874%;'
    zf_easter_position = 'left: 11.530%; top: 41.399%; width: 6.254%; height: 11.553%;'
    manual_links = [
        ('F3 page', 'https://ducklove.github.io/everything-about-nikon/f3/', 'left: 38.5%; top: 52%; width: 3%; height: 5%;'),
        ('F3 Nikon manual', 'https://cdn-10.nikon-cdn.com/pdf/manuals/archive/F3.pdf', 'left: 47.5%; top: 33%; width: 8%; height: 10%;'),
        ('Nikkormat manual', 'https://www.cameramanuals.org/nikon_pdf/nikkormat_ftn.pdf', 'left: 62.5%; top: 18%; width: 11%; height: 12%;'),
        ('FM2 manual', 'https://cdn-10.nikon-cdn.com/pdf/manuals/archive/FM2.pdf', 'left: 87.5%; top: 40%; width: 10%; height: 14%;'),
    ]
    links = []
    links.append(
        f'<a class="hero-hotspot" href="{escape(zf_manual_href)}" target="_blank" rel="noopener noreferrer" '
        f'style="{zf_manual_position}">'
        '<span class="visually-hidden">Zf manual</span>'
        '</a>'
    )
    links.append(
        f'<button class="hero-hotspot hero-hotspot--easter-egg" type="button" '
        f'data-hero-easter-egg="negative" aria-pressed="false" '
        f'aria-label="Toggle negative hero image" title="Zf negative mode" '
        f'style="{zf_easter_position}">'
        '<span class="visually-hidden">Toggle negative hero image</span>'
        '</button>'
    )
    for label, href, position in manual_links:
        links.append(
            f'<a class="hero-hotspot" href="{escape(href)}" target="_blank" rel="noopener noreferrer" style="{position}">'
            f'<span class="visually-hidden">{escape(label)}</span>'
            '</a>'
        )
    return (
        '<div class="hero-hotspots" aria-label="히어로 이미지 카메라 메뉴얼 바로가기">'
        f'{"".join(links)}'
        '</div>'
    )


def head_block(
    *,
    title: str,
    description: str,
    canonical: str,
    image_url: str,
    extra_meta: str = '',
    og_type: str = 'website',
    css_prefix: str = '',
) -> str:
    canonical_tag = f'  <link rel="canonical" href="{escape(canonical)}">\n' if canonical else ''
    og_url = f'  <meta property="og:url" content="{escape(canonical)}">\n' if canonical else ''
    return f"""<head>
  <meta charset=\"UTF-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
  <meta name=\"nikon-api-base\" content=\"{escape(api_base_url())}\">
  <title>{escape(title)}</title>
  <meta name=\"description\" content=\"{escape(description)}\">
  <meta property=\"og:type\" content=\"{og_type}\">
  <meta property=\"og:title\" content=\"{escape(title)}\">
  <meta property=\"og:description\" content=\"{escape(description)}\">
  <meta property=\"og:image\" content=\"{escape(image_url)}\">
{og_url}{canonical_tag}{ga_snippet()}
  <link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">
  <link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>
  <link href=\"https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap\" rel=\"stylesheet\">
  <link rel=\"stylesheet\" href=\"{css_prefix}css/style.css\">
{extra_meta}</head>"""


def head_block_product(*, title: str, description: str, canonical: str, image_url: str, extra_meta: str = '') -> str:
    return head_block(
        title=title,
        description=description,
        canonical=canonical,
        image_url=image_url,
        extra_meta=extra_meta,
        og_type='article',
        css_prefix='../',
    )


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
        '<div class="container site-links-container">'
        '<nav class="site-links" aria-label="사이트 바로가기">'
        f'{"".join(items)}'
        '</nav>'
        '<div id="auth-area" class="auth-area"></div>'
        '</div>'
        '</div>'
    )


# 제휴 관계 고지. eBay Partner Network 링크를 노출하는 이상 FTC·공정거래위원회의
# 추천·보증 심사지침상 대가 관계를 명시해야 하므로 모든 페이지 푸터에 노출한다.
AFFILIATE_DISCLOSURE = (
    '이 사이트의 eBay 매물 링크에는 eBay 파트너 네트워크 제휴 링크가 포함될 수 있습니다. '
    '링크를 통해 구매가 이루어지면 사이트 운영자가 eBay로부터 수수료를 받을 수 있으며, '
    '구매자가 내는 금액은 달라지지 않습니다. 제휴 여부는 매물 선정과 시세 계산에 영향을 주지 않습니다.'
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
      <p class=\"footer-note footer-note--disclosure\">{escape(AFFILIATE_DISCLOSURE)}</p>
    </div>
  </footer>"""


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


def film_hotspot_style(
    row: int,
    col: int,
    *,
    rows: int,
    cols: int,
    inset_x: float = 1.1,
    inset_y: float = 1.1,
    row_bounds: list[int] | None = None,
    image_height: int | None = None,
) -> str:
    cell_width = 100 / cols
    left = (col * cell_width) + inset_x
    width = cell_width - (inset_x * 2)

    if row_bounds and image_height:
        top_px = row_bounds[row]
        bottom_px = row_bounds[row + 1]
        band_height = ((bottom_px - top_px) / image_height) * 100
        band_inset_y = min(inset_y, max(band_height * 0.12, 0.2))
        top = (top_px / image_height) * 100 + band_inset_y
        height = max(band_height - (band_inset_y * 2), 1.4)
    else:
        cell_height = 100 / rows
        top = (row * cell_height) + inset_y
        height = cell_height - (inset_y * 2)

    return f'left: {left:.3f}%; top: {top:.3f}%; width: {width:.3f}%; height: {height:.3f}%;'


def build_film_visual_index(catalog: dict[str, Any]) -> str:
    film_category = next((category for category in catalog['categories'] if category['id'] == 'film-cameras'), None)
    if not film_category:
        return ''

    product_lookup = {
        product['id']: product
        for product in film_category['products']
    }
    boards = []

    for board in FILM_VISUAL_INDEX:
        hotspots = []
        for product_id, row, col in board['hotspots']:
            product = product_lookup.get(product_id)
            if not product:
                continue
            label = product['name_ko']
            hotspots.append(
                f'<a class="film-atlas__hotspot" href="products/{escape(product_id)}.html"'
                f' aria-label="{escape(label)} details"'
                f' title="{escape(label)}"'
                f' style="{film_hotspot_style(row, col, rows=board["rows"], cols=board["cols"], row_bounds=board.get("row_bounds"), image_height=board.get("image_height"))}">'
                f'<span class="visually-hidden">{escape(label)}</span>'
                '</a>'
            )

        boards.append(
            f"""
          <article class="film-atlas__board">
            <div class="film-atlas__image-wrap">
              <img src="{escape(board["image"])}" alt="{escape(board["alt"])}" class="film-atlas__image" loading="lazy" width="{board.get('image_width', 550)}">
              <div class="film-atlas__hotspots">{''.join(hotspots)}</div>
            </div>
          </article>"""
        )

    return f"""
    <section id="film-atlas" class="film-atlas" aria-label="Film camera visual index" hidden>
      <details class="film-atlas__details">
        <summary class="film-atlas__summary">니콘 카메라 히스토리 (1948~2005)</summary>
        <div class="film-atlas__boards">
          {''.join(boards)}
        </div>
      </details>
    </section>"""


LENS_ATLAS_CATEGORIES = {
    'z-mount-lenses': 'Z마운트 렌즈 라인업 (화각 순)',
    'f-mount-lenses': 'F마운트 렌즈 라인업 (화각 순)',
    'classic-lenses': '클래식 렌즈 라인업 (화각 순)',
}


def _build_lens_atlas_section(category: dict[str, Any], summary: str) -> str:
    products = sorted(
        category['products'],
        key=lambda p: (p.get('focal_length_min') or 9999, p.get('name_ko', '')),
    )

    cells = []
    for p in products:
        pid = p['id']
        label = p['name_ko']
        samples = p.get('samples') or []
        image_url = ''
        if samples:
            raw = samples[0].get('image', '')
            if raw:
                image_url = re.sub(r's-l\d+', 's-l225', raw)

        if image_url:
            img_tag = (
                f'<img src="{escape(image_url)}" alt="{escape(label)}"'
                f' class="lens-atlas__thumb" loading="lazy" decoding="async">'
            )
        else:
            img_tag = '<span class="lens-atlas__placeholder" aria-hidden="true">Nikon</span>'

        cells.append(
            f'<a class="lens-atlas__cell" href="products/{escape(pid)}.html"'
            f' title="{escape(label)}">'
            f'{img_tag}'
            f'<span class="lens-atlas__label">{escape(label)}</span>'
            f'</a>'
        )

    cat_id = category['id']
    return f"""
    <section id="lens-atlas-{escape(cat_id)}" class="lens-atlas" data-category-id="{escape(cat_id)}" aria-label="{escape(summary)}" hidden>
      <details class="film-atlas__details">
        <summary class="film-atlas__summary">{escape(summary)}</summary>
        <div class="lens-atlas__grid">
          {''.join(cells)}
        </div>
      </details>
    </section>"""


def build_lens_visual_index(catalog: dict[str, Any]) -> str:
    sections = []
    for category in catalog['categories']:
        summary = LENS_ATLAS_CATEGORIES.get(category['id'])
        if summary:
            sections.append(_build_lens_atlas_section(category, summary))
    return ''.join(sections)


DEAL_RADAR_MAX_ITEMS = 12


def build_deal_radar(catalog: dict[str, Any]) -> str:
    """홈 상단 '딜 레이더' 섹션. 딜 데이터가 없으면 빈 문자열을 반환한다."""
    entries = []
    for category in catalog['categories']:
        for product in category['products']:
            for deal in product.get('deals') or []:
                if not deal.get('url'):
                    continue
                entries.append((deal, product))
    if not entries:
        return ''

    entries.sort(key=lambda entry: entry[0].get('discount_pct') or 0, reverse=True)
    entries = entries[:DEAL_RADAR_MAX_ITEMS]

    cards = []
    for deal, product in entries:
        image = deal.get('image') or ''
        if image:
            image_tag = f'<img class="deal-card__image" src="{escape(image)}" alt="" loading="lazy">'
        else:
            image_tag = '<span class="deal-card__placeholder" aria-hidden="true">Nikon</span>'
        discount = float(deal.get('discount_pct') or 0)
        cards.append(
            f"""
        <a class="deal-card" href="{escape(deal.get('url', '#'))}" target="_blank" rel="noopener noreferrer nofollow">
          {image_tag}
          <div class="deal-card__info">
            <div class="deal-card__top">
              <span class="deal-card__badge">-{discount:.0f}%</span>
              <span class="deal-card__product">{escape(product['name_ko'])}</span>
            </div>
            <div class="deal-card__title">{escape(deal.get('title', ''))}</div>
            <div class="deal-card__price">{render_money_span(deal.get('price'))}<span class="deal-card__median">중앙값 {render_money_span(product.get('median'))}</span></div>
          </div>
        </a>"""
        )

    return f"""
    <section id="deal-radar" class="deal-radar" aria-labelledby="deal-radar-title">
      <div class="deal-radar__header">
        <div>
          <span class="section-kicker">Deal radar</span>
          <h2 id="deal-radar-title" class="section-heading">딜 레이더</h2>
        </div>
        <p class="deal-radar__summary">중앙값보다 20% 이상 저렴한 현재 매물입니다. 상태와 구성품을 반드시 확인하세요.</p>
      </div>
      <div class="deal-radar-grid">{''.join(cards)}</div>
    </section>"""


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
