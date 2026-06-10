#!/usr/bin/env python3
"""eBay Browse API를 사용하여 니콘 중고 장비 시세를 수집합니다.

실제 구현은 nikon_value 패키지에 있다. 이 파일은 CLI 진입점이자 기존
임포트 경로(scripts.fetch_prices)와의 호환 facade다.

주의: 여기서 재노출된 이름을 monkeypatch해도 패키지 내부 호출에는
반영되지 않는다. 테스트에서 패치할 때는 실제 정의 모듈
(예: nikon_value.ebay)을 대상으로 한다.
"""

import sys
from pathlib import Path

# `python scripts/fetch_prices.py`로 직접 실행하면 sys.path에 저장소 루트가
# 없으므로 nikon_value 패키지를 찾을 수 있도록 보장한다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nikon_value.ebay import (  # noqa: E402,F401
    ADAPTIVE_MAX_PRICE_STEPS,
    ADAPTIVE_MAX_PRICE_TRIGGER_RATIO,
    EBAY_AUTH_URL_PROD,
    EBAY_AUTH_URL_SANDBOX,
    EBAY_BROWSE_URL_PROD,
    EBAY_BROWSE_URL_SANDBOX,
    RATE_LIMIT_BASE_WAIT_SECONDS,
    RATE_LIMIT_MAX_RETRIES,
    RATE_LIMIT_MAX_WAIT_SECONDS,
    collect_prices,
    extract_price,
    get_access_token,
    get_ebay_urls,
    round_price_bound,
    search_items,
    search_items_for_product,
    should_expand_max_price,
)
from nikon_value.env import load_env_file, load_text_secret  # noqa: E402,F401
from nikon_value.exchange import (  # noqa: E402,F401
    ECB_EXCHANGE_RATES_URL,
    _recover_exchange_rate_from_daily,
    fetch_usd_krw_exchange_rate,
)
from nikon_value.fetch import main, parse_args, parse_only_ids  # noqa: E402,F401
from nikon_value.filters import (  # noqa: E402,F401
    ACCESSORY_ALLOWED_PATTERNS,
    AF_TOKEN_RE,
    AI_S_TOKEN_RE,
    AI_TOKEN_RE,
    CAMERA_BODY_EXCLUDE_PATTERNS,
    COMMON_EXCLUDE_PATTERNS,
    LENS_HOOD_RE,
    NON_AI_TOKEN_RE,
    SERIES_E_TOKEN_RE,
    filter_items_with_rules,
    get_title_variant_group,
    is_camera_body_product,
    is_obvious_non_match,
    is_variant_conflict,
    matches_product_exclude_patterns,
    normalize_title,
)
from nikon_value.llm import (  # noqa: E402,F401
    OPENROUTER_API_URL,
    OPENROUTER_DEFAULT_MODEL,
    _openrouter_model,
    extract_openrouter_indices,
    extract_openrouter_message_text,
    filter_items_with_llm,
    load_openrouter_key,
    strip_json_code_fence,
)
from nikon_value.paths import CONFIG_PATH, DATA_DIR, PROJECT_ROOT  # noqa: E402,F401
from nikon_value.stats import compute_stats, extract_sample_listings  # noqa: E402,F401
from nikon_value.storage import (  # noqa: E402,F401
    MAX_DAILY_SNAPSHOTS,
    MAX_PRODUCT_HISTORY,
    build_base_product_entry,
    cleanup_daily_snapshots,
    load_catalog,
    load_daily_snapshot_for_date,
    load_existing_catalog_output,
    update_product_history,
)

if __name__ == "__main__":
    main()
