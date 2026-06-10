"""규칙 기반 매물 타이틀 필터.

LLM 필터 이전에 명백한 비매칭(부품, 액세서리, 다른 세대 수동 렌즈 등)을
키워드·정규식으로 제거한다.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

COMMON_EXCLUDE_PATTERNS = [
    " for parts",
    " parts only",
    " not working",
    " broken",
    " repair",
    " manual",
    " instruction",
    " empty box",
    " box only",
    " packaging only",
    " body cap",
    " rear cap",
    " front cap",
    " battery",
    " charger",
    " strap",
    " adapter",
    " filter",
    " grip",
    " eyepiece",
    " focusing screen",
    " viewfinder",
    " motor drive",
    " screen protector",
    " camera case",
    " lens case",
    " bag only",
    " case only",
    " cap only",
    " bundle",
    " issues",
    " issue",
    " untested",
    " as is",
    " as-is",
    " junk",
]
ACCESSORY_ALLOWED_PATTERNS = {
    " focusing screen",
    " viewfinder",
    " motor drive",
}
LENS_HOOD_RE = re.compile(r"\b(?:hood|shade|hb-\d+|hn-\d+|hr-\d+|hs-\d+|hk-\d+|he-\d+|hf-\d+)\b")
CAMERA_BODY_EXCLUDE_PATTERNS = [
    " lens ",
    " nikkor ",
    " sigma ",
    " tamron ",
    " tokina ",
    " teleconverter ",
    " tc-",
    " lens kit",
    " kit lens",
]
AI_S_TOKEN_RE = re.compile(r"\b(?:ai-s|ai s|ais)\b")
AI_TOKEN_RE = re.compile(r"\bai\b")
NON_AI_TOKEN_RE = re.compile(r"\b(?:non[- ]ai|new nikkor|nikkor-[a-z.]+ auto|nikkor [a-z.]+ auto|auto)\b")
AF_TOKEN_RE = re.compile(r"\b(?:af(?:-s|-p|-d)?|af nikkor|autofocus|auto focus)\b")
SERIES_E_TOKEN_RE = re.compile(r"\b(?:series e|e series)\b")


def normalize_title(title: str) -> str:
    """간단한 키워드 매칭을 위해 타이틀을 정규화합니다."""
    text = re.sub(r"[^a-z0-9/+.-]+", " ", title.lower())
    return f" {text} "


def matches_product_exclude_patterns(normalized_title: str, product: dict) -> bool:
    """Product-specific title fragments that should always exclude a listing."""
    patterns = product.get("exclude_title_patterns") or []
    for pattern in patterns:
        if pattern and normalize_title(str(pattern)) in normalized_title:
            return True
    return False


def get_title_variant_group(product: dict) -> str | None:
    """제품 ID를 바탕으로 수동 렌즈 세대 그룹을 판별합니다."""
    pid = product.get("id", "")
    if pid.startswith("ai-s-"):
        return "ai-s"
    if pid.startswith("series-e-"):
        return "series-e"
    if pid.startswith("nikkor-auto-") or pid.startswith("micro-nikkor-auto-") or pid.startswith("noct-nikkor-"):
        return "non-ai"
    if pid.startswith("nikkor-") and pid.endswith("-ai"):
        return "ai"
    return None


def is_variant_conflict(title: str, product: dict) -> bool:
    """수동 렌즈 세대가 다른 매물인지 판별합니다."""
    variant_group = get_title_variant_group(product)
    if not variant_group:
        return False

    normalized = normalize_title(title)
    has_ai_s = bool(AI_S_TOKEN_RE.search(normalized))
    has_ai = bool(AI_TOKEN_RE.search(normalized))
    has_non_ai = bool(NON_AI_TOKEN_RE.search(normalized))
    has_af = bool(AF_TOKEN_RE.search(normalized))
    has_series_e = bool(SERIES_E_TOKEN_RE.search(normalized))

    if variant_group == "ai-s":
        return has_non_ai or has_af or has_series_e or not has_ai_s

    if variant_group == "ai":
        return has_non_ai or has_af or has_series_e or has_ai_s or not has_ai

    if variant_group == "non-ai":
        return has_af or has_ai_s or has_series_e or (has_ai and not has_non_ai)

    if variant_group == "series-e":
        return has_af or not has_series_e

    return False


def is_camera_body_product(product: dict) -> bool:
    """카메라 바디 분류인지 판별합니다."""
    return product.get("category_id") in {"31388", "3323"}


def is_obvious_non_match(title: str, product: dict) -> bool:
    """명백한 비매칭/액세서리 매물을 규칙 기반으로 제거합니다."""
    normalized = normalize_title(title)
    exclude_patterns = COMMON_EXCLUDE_PATTERNS
    if product.get("product_type") == "accessory":
        exclude_patterns = [
            pattern for pattern in COMMON_EXCLUDE_PATTERNS
            if pattern not in ACCESSORY_ALLOWED_PATTERNS
        ]

    if any(pattern in normalized for pattern in exclude_patterns):
        return True
    if matches_product_exclude_patterns(normalized, product):
        return True
    if LENS_HOOD_RE.search(normalized):
        return True
    if is_variant_conflict(title, product):
        return True

    if is_camera_body_product(product):
        if any(pattern in normalized for pattern in CAMERA_BODY_EXCLUDE_PATTERNS):
            return True

    return False


def filter_items_with_rules(items: list[dict], product: dict) -> list[dict]:
    """LLM 이전에 명백한 비매칭을 제거합니다."""
    if not items:
        return items

    filtered = [
        item
        for item in items
        if not is_obvious_non_match(item.get("title", ""), product)
    ]

    if not filtered:
        log.warning("  Rule filter removed all items, keeping original set")
        return items

    if len(filtered) != len(items):
        log.info("  Rule filter: %d → %d items", len(items), len(filtered))

    return filtered
