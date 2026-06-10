#!/usr/bin/env python3
"""Build the static GitHub Pages artifact for the Nikon value tracker.

실제 구현은 nikon_value.sitegen 패키지에 있다. 이 파일은 CLI 진입점이자
기존 임포트 경로(scripts.build_static_site)와의 호환 facade다.
"""

import sys
from pathlib import Path

# `python scripts/build_static_site.py`로 직접 실행하면 저장소 루트가
# sys.path에 없으므로 nikon_value 패키지를 찾도록 보장한다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nikon_value.sitegen.build import (  # noqa: E402,F401
    AUTH_JS_PATH,
    DEFAULT_OUTPUT,
    EBAY_LOGO,
    FILM_HISTORY_JPG_1,
    FILM_HISTORY_JPG_2,
    HERO_JPG,
    HERO_WEBP_800,
    HERO_WEBP_1600,
    LEGACY_ROOT_FILES_TO_REMOVE,
    ROOT_FILES_TO_PUBLISH,
    ROOT_PRODUCTS_DIR,
    SITE_JS_PATH,
    STYLE_PATH,
    clean_output,
    copy_assets,
    detect_base_url,
    ensure_dir,
    main,
    parse_args,
    publish_root_site,
)
from nikon_value.sitegen.components import (  # noqa: E402,F401
    DEFAULT_API_BASE_URL,
    FILM_VISUAL_INDEX,
    GA_MEASUREMENT_ID,
    LENS_ATLAS_CATEGORIES,
    api_base_url,
    build_currency_toggle,
    build_film_visual_index,
    build_footer,
    build_hero_manual_hotspots,
    build_lens_visual_index,
    build_product_reference_cards,
    build_site_links,
    film_hotspot_style,
    ga_snippet,
    head_block,
    head_block_product,
    product_image,
    render_product_offer_schema,
)
from nikon_value.sitegen.data import (  # noqa: E402,F401
    BODY_CATEGORIES,
    CATALOG_PATH,
    CONFIG_PATH,
    DATA_DIR,
    PROJECT_ROOT,
    RARITY_FIELDS,
    compute_price_change,
    compute_stale_days,
    has_catalog_listing_data,
    is_lens_category,
    load_catalog,
    load_catalog_config,
    load_history,
    merge_catalog_with_config,
    should_show_home_catalog_product,
    sort_products,
)
from nikon_value.sitegen.format import (  # noqa: E402,F401
    format_change_percent,
    format_change_value,
    format_exchange_rate,
    format_exchange_rate_inline,
    format_money,
    json_script,
    render_money_range,
    render_money_span,
)
from nikon_value.sitegen.pages import (  # noqa: E402,F401
    build_404_page,
    build_home_page,
    build_product_page,
    build_resources_page,
    build_robots,
    build_sitemap,
)

if __name__ == '__main__':
    main()
