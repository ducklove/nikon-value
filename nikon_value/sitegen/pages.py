"""홈/제품/리소스/404 페이지와 sitemap·robots 문서 빌더."""

from __future__ import annotations

from html import escape
from typing import Any

from nikon_value.sitegen.components import (
    build_currency_toggle,
    build_deal_radar,
    build_film_visual_index,
    build_footer,
    build_hero_manual_hotspots,
    build_lens_visual_index,
    build_liquidity_section,
    build_product_reference_cards,
    build_site_links,
    head_block,
    head_block_product,
    product_image,
    render_product_offer_schema,
)
from nikon_value.sitegen.data import (
    compute_price_change,
    compute_stale_days,
    has_catalog_listing_data,
    is_at_yearly_low,
    should_show_home_catalog_product,
    sort_products,
)
from nikon_value.sitegen.format import (
    format_change_percent,
    format_exchange_rate_inline,
    format_money,
    json_script,
    render_money_range,
    render_money_span,
)
from nikon_value.sitegen.liquidity import (
    LIQUIDITY_WINDOW_DAYS,
    build_card_scarcity,
    compute_liquidity,
)

# 제품 페이지에 인라인으로 싣는 히스토리 포인트 수.
# <noscript> 히스토리 표가 쓰는 구간과 같은 값이라 SEO·접근성 요구를 그대로 만족한다.
INLINE_HISTORY_POINTS = 10


def build_home_page(catalog: dict[str, Any], base_url: str, histories: dict[str, list[dict[str, Any]]] | None = None) -> str:
    updated = catalog['updated']
    exchange_rate = catalog.get('exchange_rate')
    stale_days = compute_stale_days(updated)
    total_products = sum(
        1
        for category in catalog['categories']
        for product in category['products']
        if should_show_home_catalog_product(category['id'], product)
    )
    total_listings = sum(
        (product.get('count') or 0)
        for category in catalog['categories']
        for product in category['products']
        if has_catalog_listing_data(product)
    )
    total_categories = len(catalog['categories'])
    stale_banner = ''
    if stale_days >= 2:
        stale_banner = f"""
      <div class=\"stale-banner\" role=\"status\">
        <strong>데이터 점검 필요</strong>
        <span>마지막 업데이트가 {stale_days}일 전({escape(updated)})입니다. 자동 수집 워크플로 상태를 확인하세요.</span>
      </div>"""

    tabs = ['<button class="category-tab active" type="button" data-category-id="all">전체</button>']
    feature_order = 0
    cards_data = []
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
            if not should_show_home_catalog_product(category['id'], product):
                continue
            feature_order += 1
            samples = product.get('samples') or []
            thumb = samples[0]['image'] if samples and samples[0].get('image') else None
            category_label = category['name_ko']
            if product.get('subcategory') and subcategory_lookup.get(product['subcategory']):
                category_label = f"{category_label} / {subcategory_lookup[product['subcategory']]}"
            badge_value = product.get('release_year') or (
                f'{product["focal_length_min"]}mm' if product.get('focal_length_min') else ''
            )
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
            cards_data.append({
                'id': product['id'],
                'name_ko': product['name_ko'],
                'name_en': product['name_en'],
                'category_id': category['id'],
                'category_label': category_label,
                'search': search_index,
                'median': product.get('median'),
                'q1': product.get('q1'),
                'q3': product.get('q3'),
                'count': product.get('count') or 0,
                'thumb': thumb,
                'feature_order': feature_order,
                'priority': priority_value,
                'badge': str(badge_value) if badge_value else None,
                'is_rare': bool(product.get('is_rare')),
                'rarity_tier': product.get('rarity_tier'),
                'rarity_sort': product.get('rarity_sort'),
                'rarity_price_hint': product.get('rarity_price_hint'),
                'rarity_note': product.get('rarity_note'),
                'delta_pct': None,
                'at_low': False,
                # 희소 신호가 있을 때만 채운다. 295개 카드에 '풍부'를 붙이는 건
                # 정보가 아니라 노이즈다(nikon_value/sitegen/liquidity.py 주석 참고).
                'scarcity': None,
            })
            if histories:
                history = histories.get(product['id'], [])
                change = compute_price_change(history, 30)
                if change:
                    cards_data[-1]['delta_pct'] = round(change['delta_pct'], 1)
                cards_data[-1]['at_low'] = is_at_yearly_low(history)
                cards_data[-1]['scarcity'] = build_card_scarcity(compute_liquidity(history))

    tabs.append('<button class="category-tab" type="button" data-category-id="favorites" id="favorites-tab" hidden>관심 목록</button>')

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

    return f"""<!DOCTYPE html>
<html lang=\"ko\">
{head_block(title='니콘 중고 시세 트래커', description=description, canonical=canonical, image_url=image_url, extra_meta=extra_meta)}
<body data-page=\"catalog\">
  <header class=\"site-header\">
    <div class=\"hero-banner\" id=\"hero-banner\">
      <picture class=\"hero-picture\" id=\"hero-picture-default\">
        <source type=\"image/webp\" srcset=\"assets/mynikons-800.webp 800w, assets/mynikons-1600.webp 1600w\" sizes=\"100vw\">
        <img src=\"mynikons.jpg\" alt=\"Nikon camera collection\" class=\"hero-image\" width=\"1600\" height=\"900\" fetchpriority=\"high\" loading=\"eager\" decoding=\"async\">
      </picture>
      <img src=\"assets/nikon-lens-lineup.jpg\" alt=\"Nikkor lens lineup\" class=\"hero-image hero-image--lens\" id=\"hero-image-lens\" width=\"1123\" height=\"405\" loading=\"eager\" decoding=\"async\" hidden>
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
    <div class=\"container category-nav__container\">
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
    </section>{stale_banner}{build_deal_radar(catalog)}

    <section id=\"rare-watch\" class=\"rare-watch\" aria-labelledby=\"rare-watch-title\" hidden>
      <div class=\"rare-watch__header\">
        <div>
          <span class=\"section-kicker\">Rare listing watch</span>
          <h2 id=\"rare-watch-title\" class=\"section-heading\">희귀 매물 감지</h2>
        </div>
        <p id=\"rare-watch-summary\" class=\"rare-watch__summary\"></p>
      </div>
      <div id=\"rare-watch-grid\" class=\"rare-watch-grid\"></div>
    </section>

    {build_film_visual_index(catalog)}
    {build_lens_visual_index(catalog)}

    <div id=\"product-grid\" class=\"product-grid\">{''.join(['<div class="product-card product-card--skeleton" aria-hidden="true"><div class="product-card__thumb-placeholder skeleton-pulse"></div><div class="product-card__body"><div class="skeleton-line skeleton-line--title"></div><div class="skeleton-line skeleton-line--subtitle"></div><div class="skeleton-line skeleton-line--price"></div><div class="skeleton-line skeleton-line--meta"></div></div></div>'] * 8)}</div>
    <p id=\"catalog-empty\" class=\"empty-state-inline\" hidden>조건에 맞는 제품이 없습니다.</p>
  </main>
{build_footer()}

  <script id=\"exchange-rate-data\" type=\"application/json\">{json_script(exchange_rate or {})}</script>
  <script id=\"cards-data\" type=\"application/json\">{json_script(cards_data)}</script>
  <script src=\"js/site.js\" defer></script>
  <script src=\"js/auth.js\" defer></script>
</body>
</html>
"""


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
    liquidity = compute_liquidity(history)
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

    # 전체 히스토리는 dist/data/products/{id}.json으로도 배포되므로 인라인은 최근 구간만 싣는다
    # (같은 데이터를 산출물에 두 번 싣지 않기 위한 축소).
    #
    # js/site.js와의 폴백 계약:
    #   1) 제품 페이지는 body[data-history-url]이 가리키는 전체 히스토리 JSON을 fetch해 차트를 그린다.
    #   2) fetch가 실패하거나(오프라인·404·파싱 오류) 응답이 비어 있으면
    #      <script id="history-data">의 인라인 데이터로 폴백한다.
    #      인라인 INLINE_HISTORY_POINTS건만으로도 차트가 최소한 그려져야 한다.
    #   3) 두 데이터의 스키마는 동일하다 — 인라인은 전체 히스토리의 꼬리 구간이다.
    inline_history = history[-INLINE_HISTORY_POINTS:]
    history_url = f"../data/products/{product['id']}.json"
    # 비교 뷰 진입 동선: 지금 보고 있는 모델을 담은 채로 비교 페이지를 연다.
    # 비교 페이지에서 나머지 모델을 검색으로 추가하는 흐름이라 여기서는 1개만 넘긴다.
    compare_href = f"../compare.html?ids={product['id']}"

    history_rows = []
    for entry in reversed(inline_history):
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
    if is_at_yearly_low(history):
        meta_pills.append('<span class="meta-pill meta-pill--low">1년 내 최저</span>')
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
<body data-page=\"product\" data-default-period=\"180\" data-history-url=\"{escape(history_url)}\">
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
{build_liquidity_section(liquidity)}

    <section class=\"chart-section\">
      <div class=\"chart-header\">
        <h2>시세 추이</h2>
        <a class=\"compare-cta\" href=\"{escape(compare_href)}\">다른 모델과 비교 &rarr;</a>
        <div class=\"period-selector\">
          <button class=\"period-btn\" type=\"button\" data-period=\"30\">1개월</button>
          <button class=\"period-btn\" type=\"button\" data-period=\"90\">3개월</button>
          <button class=\"period-btn active\" type=\"button\" data-period=\"180\">6개월</button>
          <button class=\"period-btn\" type=\"button\" data-period=\"365\">1년</button>
          <button class=\"period-btn\" type=\"button\" data-period=\"0\">전체</button>
        </div>
        <button class=\"ma-toggle\" type=\"button\" aria-pressed=\"false\" title=\"7일 이동평균선 표시\">7일 평균</button>
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
  <script id=\"history-data\" type=\"application/json\">{json_script(inline_history)}</script>
  <script src=\"https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js\" defer></script>
  <script src=\"../js/site.js\" defer></script>
  <script src=\"../js/auth.js\" defer></script>
</body>
</html>
"""


# 한 차트에 겹칠 수 있는 최대 제품 수. URL 파라미터는 신뢰할 수 없는 입력이므로
# 파이썬(문구)과 js/compare.js(실제 절단)가 같은 값을 쓴다.
COMPARE_MAX_PRODUCTS = 5


def build_compare_page(catalog: dict[str, Any], base_url: str) -> str:
    """모델 비교 페이지.

    색인 정책
    --------------------------------------------------------------------------
    ?ids= 조합은 사실상 무한이라 sitemap에는 넣지 않는다. robots.txt로 막지는
    않는데, Disallow하면 크롤러가 페이지를 열지 못해 아래 noindex 메타를 볼 수
    없어 오히려 URL이 색인에 남을 수 있기 때문이다. 대신 noindex, follow로
    "색인하지 말고 링크는 따라가라"를 명시하고, canonical은 파라미터 없는
    compare.html로 고정한다.
    """
    canonical = f'{base_url}/compare.html' if base_url else ''
    image_url = f'{base_url}/assets/mynikons-1600.webp' if base_url else 'assets/mynikons-1600.webp'
    description = '니콘 중고 시세를 모델별로 겹쳐 비교합니다. 최대 5개 모델의 중앙값·매물 수 추이를 한 차트에서 볼 수 있습니다.'
    exchange_rate = catalog.get('exchange_rate')

    # 비교 대상 선택기와 ID 검증에 쓰는 목록. 썸네일·샘플은 빼서 페이로드를 줄인다.
    compare_products = []
    for category in catalog['categories']:
        for product in sort_products(category['products'], category['id']):
            compare_products.append({
                'id': product['id'],
                'name_ko': product['name_ko'],
                'name_en': product['name_en'],
                'category_id': category['id'],
                'category_label': category['name_ko'],
                'median': product.get('median'),
                'count': product.get('count') or 0,
            })

    extra_meta = '  <meta name="robots" content="noindex, follow">\n'

    return f"""<!DOCTYPE html>
<html lang=\"ko\">
{head_block(title='모델 비교 - 니콘 중고 시세 트래커', description=description, canonical=canonical, image_url=image_url, extra_meta=extra_meta)}
<body data-page=\"compare\" data-compare-max=\"{COMPARE_MAX_PRODUCTS}\">
  <header class=\"site-header\">
    <div class=\"container\">
      <a href=\"index.html\" class=\"back-link\">&larr; 전체 목록</a>
      <h1 class=\"product-title\">모델 비교</h1>
      <p class=\"product-subtitle\">최대 {COMPARE_MAX_PRODUCTS}개 모델의 시세 추이를 한 차트에 겹쳐 봅니다.</p>
    </div>
  </header>
  {build_site_links('compare')}

  <main class=\"container\">
    <section class=\"compare-picker\" aria-labelledby=\"compare-picker-title\">
      <div class=\"section-header-row\">
        <div>
          <span class=\"section-kicker\">Pick models</span>
          <h2 id=\"compare-picker-title\" class=\"section-heading\">비교할 모델</h2>
        </div>
        {build_currency_toggle(exchange_rate, compact=True)}
      </div>
      <div class=\"compare-search\">
        <label class=\"visually-hidden\" for=\"compare-search-input\">비교할 모델 검색</label>
        <input class=\"search-input\" id=\"compare-search-input\" type=\"search\" role=\"combobox\" aria-expanded=\"false\" aria-autocomplete=\"list\" aria-controls=\"compare-suggestions\" autocomplete=\"off\" placeholder=\"모델명을 입력해 추가 (예: Z8, 50mm)\">
        <ul class=\"compare-suggestions\" id=\"compare-suggestions\" role=\"listbox\" aria-label=\"검색 결과\" hidden></ul>
      </div>
      <ul class=\"compare-chips\" id=\"compare-chips\" aria-label=\"선택한 모델\"></ul>
      <p class=\"detail-note\" id=\"compare-status\" role=\"status\"></p>
    </section>

    <section class=\"chart-section\" aria-labelledby=\"compare-chart-title\">
      <div class=\"chart-header\">
        <h2 id=\"compare-chart-title\">시세 추이 비교</h2>
        <div class=\"period-selector\" id=\"compare-periods\">
          <button class=\"period-btn\" type=\"button\" data-period=\"30\">1개월</button>
          <button class=\"period-btn\" type=\"button\" data-period=\"90\">3개월</button>
          <button class=\"period-btn active\" type=\"button\" data-period=\"180\">6개월</button>
          <button class=\"period-btn\" type=\"button\" data-period=\"365\">1년</button>
          <button class=\"period-btn\" type=\"button\" data-period=\"0\">전체</button>
        </div>
        <div class=\"compare-modes\" role=\"group\" aria-label=\"비교 지표 선택\">
          <button class=\"period-btn active\" type=\"button\" data-metric=\"median\">중앙값</button>
          <button class=\"period-btn\" type=\"button\" data-metric=\"count\">매물 수</button>
          <button class=\"ma-toggle\" type=\"button\" id=\"compare-indexed\" aria-pressed=\"false\" title=\"각 모델의 첫 관측값을 100으로 맞춰 변동률만 비교합니다\">지수화 (첫날=100)</button>
        </div>
      </div>
      <div class=\"chart-container\">
        <canvas id=\"compare-chart\"></canvas>
        <p class=\"chart-empty\" id=\"compare-chart-empty\">비교할 모델을 2개 이상 추가하세요.</p>
      </div>
      <div class=\"compare-legend\" id=\"compare-legend\"></div>
    </section>

    <section class=\"compare-table-section\" aria-labelledby=\"compare-table-title\">
      <div class=\"section-header-row\">
        <div>
          <span class=\"section-kicker\">Side by side</span>
          <h2 id=\"compare-table-title\" class=\"section-heading\">현재 시세 비교</h2>
        </div>
      </div>
      <div class=\"compare-table-wrap\">
        <table class=\"history-table compare-table\" id=\"compare-table\">
          <thead>
            <tr><th>모델</th><th>중앙값</th><th>현재 매물</th><th>{LIQUIDITY_WINDOW_DAYS}일 평균 매물</th><th>기간 변동</th></tr>
          </thead>
          <tbody id=\"compare-table-body\"></tbody>
        </table>
      </div>
    </section>
  </main>
{build_footer()}

  <script id=\"exchange-rate-data\" type=\"application/json\">{json_script(exchange_rate or {})}</script>
  <script id=\"compare-products\" type=\"application/json\">{json_script(compare_products)}</script>
  <script src=\"js/site.js\" defer></script>
  <script src=\"js/auth.js\" defer></script>
  <script src=\"js/compare.js\" defer></script>
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
            'MIR Nikon SLR System Guide',
            'https://www.mir.com.my/rb/photography/companies/nikon/htmls/models/htmls/slrmain8090.htm',
            '1980-1990년대 니콘 SLR 시스템 구성과 주변기기 호환성을 정리한 아카이브입니다.',
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
  <script src=\"js/auth.js\" defer></script>
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
