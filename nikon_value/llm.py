"""OpenRouter LLM 보조 필터.

규칙 필터를 통과한 매물 타이틀을 LLM으로 재분류해 정확도를 높인다.
LLM 응답이 비정상이거나 전부를 걸러내면 규칙 필터 결과를 유지한다(폴백).

판정 결과는 LlmDecisionCache에 지속되고, 미캐시 타이틀만 LLM에 보낸다.
캐시로 판정이 전부 채워진 경우에도 "자동 필터는 틀릴 수 있으므로 데이터를
지우지 않는다"는 폴백 규율은 동일하게 적용된다.
"""

from __future__ import annotations

import json
import logging
import os

import requests

from nikon_value.env import load_text_secret
from nikon_value.llm_cache import LlmDecisionCache
from nikon_value.metrics import RunMetrics, resolve_metrics

log = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_DEFAULT_MODEL = "openai/gpt-5.4-nano"


def _openrouter_model() -> str:
    """환경변수 OPENROUTER_MODEL로 모델을 지정할 수 있습니다."""
    return os.environ.get("OPENROUTER_MODEL", OPENROUTER_DEFAULT_MODEL)


def load_openrouter_key() -> str | None:
    """openrouter.key 파일 또는 OPENROUTER_API_KEY 환경변수에서 API 키를 로드합니다."""
    return load_text_secret("openrouter.key", "OPENROUTER_API_KEY")


def extract_openrouter_message_text(data: dict) -> str:
    """OpenRouter chat completion payload에서 텍스트 응답을 추출합니다."""
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if not text and isinstance(item.get("content"), str):
                    text = item["content"]
                if text:
                    parts.append(text)
        joined = "".join(parts).strip()
        if joined:
            return joined

    raise ValueError("OpenRouter response did not contain text content")


def strip_json_code_fence(text: str) -> str:
    """Remove optional ```json fenced wrappers around structured output."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def extract_openrouter_indices(data: dict) -> list[int]:
    """Parse structured JSON indices from an OpenRouter response."""
    payload = json.loads(strip_json_code_fence(extract_openrouter_message_text(data)))
    if isinstance(payload, list):
        indices = payload
    elif isinstance(payload, dict):
        indices = payload.get("indices")
    else:
        raise ValueError("OpenRouter response JSON must be a list or object")

    if not isinstance(indices, list):
        raise ValueError("OpenRouter response is missing an indices list")

    return indices


def build_llm_prompt(titles: list[str], product: dict) -> str:
    """판정 대상 타이틀 목록으로 분류 프롬프트를 만듭니다."""
    listings_text = "\n".join(f"{i}: \"{t}\"" for i, t in enumerate(titles))

    is_accessory = product.get("product_type") == "accessory"
    exclude_lines = [
        "- Different camera/lens/accessory models",
        "- Accessories, grips, batteries, straps, caps, filters, adapters, cases",
        "- Kits or bundles (unless the product itself is a kit)",
        "- Parts, repairs, or \"for parts\" listings",
        "- Manuals, boxes, or packaging only",
    ]
    if is_accessory:
        exclude_lines.insert(
            1,
            "- Camera bodies, lenses, and unrelated accessories must be excluded",
        )
    else:
        exclude_lines.insert(
            1,
            "- IMPORTANT: Lens hoods MUST be excluded. Any title containing \"hood\", \"shade\", or hood model numbers (HB-*, HN-*, HR-*, HS-*, HK-*, HE-*, HF-*) is NOT the lens itself — exclude it even if the lens name appears in the title",
        )
        exclude_lines.insert(
            3,
            "- Viewfinders, focusing screens, eyepieces, motor drives, and other camera body parts sold separately",
        )

    prompt = (
        "You are a camera/lens equipment expert. "
        "I need to find listings that are selling exactly this product:\n"
        f"Product: {product['name_en']}\n"
        f"Search query used: {product['query']}\n\n"
        "Below are eBay listing titles. Return ONLY a JSON object in this form:\n"
        "{\"indices\": [0, 2, 4]}\n"
        "Use 0-based indices for listings that ARE actually selling this specific product.\n\n"
        "Exclude:\n"
        + "\n".join(exclude_lines)
        + "\n"
        + ("- Lens-only listings when the product is a camera body\n" if not is_accessory else "")
        + "\n"
        f"Listings:\n{listings_text}"
    )
    return prompt


def _request_llm_indices(prompt: str, openrouter_key: str) -> list[int]:
    """OpenRouter에 분류를 요청하고 통과 인덱스 목록을 받습니다."""
    resp = requests.post(
        OPENROUTER_API_URL,
        headers={
            "Authorization": f"Bearer {openrouter_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://ducklove.github.io/nikon-value",
            "X-Title": "Nikon Value",
        },
        json={
            "model": _openrouter_model(),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a camera gear classifier. "
                        "Return only a JSON object with an indices array."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": 512,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return extract_openrouter_indices(resp.json())


def _keep_heuristic_set(items: list[dict]) -> list[dict]:
    """폴백: 자동 필터는 틀릴 수 있으므로 규칙 필터 결과를 그대로 유지한다."""
    if len(items) <= 5:
        log.info("  LLM filtered all %d items — accepting (small set)", len(items))
    else:
        log.warning("  LLM filtered all %d items — suspicious, keeping heuristic-filtered set", len(items))
    return items


def _partition_by_cache(
    titles: list[str],
    product_id: str,
    cache: LlmDecisionCache | None,
    run_metrics: RunMetrics,
) -> tuple[list[int], list[int]]:
    """타이틀을 (캐시가 통과시킨 인덱스, LLM에 물어야 할 인덱스)로 나눕니다."""
    cached_keep: list[int] = []
    pending: list[int] = []

    for index, title in enumerate(titles):
        decision = cache.lookup(product_id, title) if cache is not None else None
        if decision is None:
            pending.append(index)
        else:
            run_metrics.record_llm_cache_hits()
            if decision:
                cached_keep.append(index)

    run_metrics.record_llm_cache_misses(len(pending))
    return cached_keep, pending


def _resolve_llm_keep(indices: list, pending: list[int]) -> set[int]:
    """LLM이 돌려준 인덱스를 원본 인덱스로 되돌립니다(범위 밖·비정수는 무시)."""
    return {pending[i] for i in indices if isinstance(i, int) and 0 <= i < len(pending)}


def _record_decisions(
    cache: LlmDecisionCache | None,
    product_id: str,
    titles: list[str],
    pending: list[int],
    llm_keep: set[int],
) -> None:
    """이번에 LLM이 판정한 타이틀만 캐시에 기록합니다."""
    if cache is None:
        return
    for index in pending:
        cache.record(product_id, titles[index], index in llm_keep)


def _filter_with_llm_call(
    items: list[dict],
    titles: list[str],
    product: dict,
    openrouter_key: str,
    cached_keep: list[int],
    pending: list[int],
    cache: LlmDecisionCache | None,
    run_metrics: RunMetrics,
) -> list[dict]:
    """미캐시 타이틀을 LLM에 물어 캐시 판정과 합칩니다.

    응답이 비정상이거나 호출이 실패하면 규칙 필터 결과를 그대로 유지한다.
    폴백이 발동한 판정은 신뢰할 수 없으므로 캐시에도 남기지 않는다.
    """
    prompt = build_llm_prompt([titles[i] for i in pending], product)

    try:
        run_metrics.record_llm_call()
        indices = _request_llm_indices(prompt, openrouter_key)

        if not isinstance(indices, list):
            log.warning("  LLM returned non-list, skipping filter")
            return items

        llm_keep = _resolve_llm_keep(indices, pending)
        filtered = [items[i] for i in sorted(set(cached_keep) | llm_keep)]

        if not filtered:
            # 폴백 발동 — 판정을 신뢰할 수 없으므로 캐시에도 남기지 않는다.
            return _keep_heuristic_set(items)

        _record_decisions(cache, product.get("id", ""), titles, pending, llm_keep)
        return filtered

    except Exception as e:
        log.warning("  LLM filter failed (%s), keeping heuristic-filtered set", e)
        return items


def filter_items_with_llm(
    items: list[dict],
    product: dict,
    openrouter_key: str,
    cache: LlmDecisionCache | None = None,
    metrics: RunMetrics | None = None,
) -> list[dict]:
    """OpenRouter API를 사용하여 리스팅 타이틀이 실제 해당 제품인지 검증합니다.

    캐시가 주어지면 미캐시 타이틀만 LLM에 보내고 응답을 캐시에 반영한다.
    폴백(전부 탈락·호출 실패)이 발동한 판정은 신뢰할 수 없으므로 캐시에
    기록하지 않는다.
    """
    if not items:
        return items

    run_metrics = resolve_metrics(metrics)
    product_id = product.get("id", "")
    titles = [item.get("title", "") for item in items]
    cached_keep, pending = _partition_by_cache(titles, product_id, cache, run_metrics)

    # 전부 캐시 히트 → LLM 호출 없이 판정. 폴백 규율은 동일하게 적용한다.
    if not pending:
        filtered = [items[i] for i in cached_keep]
        return filtered or _keep_heuristic_set(items)

    return _filter_with_llm_call(
        items, titles, product, openrouter_key, cached_keep, pending, cache, run_metrics
    )
