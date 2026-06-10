"""OpenRouter LLM 보조 필터.

규칙 필터를 통과한 매물 타이틀을 LLM으로 재분류해 정확도를 높인다.
LLM 응답이 비정상이거나 전부를 걸러내면 규칙 필터 결과를 유지한다(폴백).
"""

from __future__ import annotations

import json
import logging
import os

import requests

from nikon_value.env import load_text_secret

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


def filter_items_with_llm(
    items: list[dict], product: dict, openrouter_key: str
) -> list[dict]:
    """OpenRouter API를 사용하여 리스팅 타이틀이 실제 해당 제품인지 검증합니다."""
    if not items:
        return items

    titles = [item.get("title", "") for item in items]
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

    try:
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
        data = resp.json()

        indices = extract_openrouter_indices(data)

        if not isinstance(indices, list):
            log.warning("  LLM returned non-list, skipping filter")
            return items

        valid_indices = [i for i in indices if isinstance(i, int) and 0 <= i < len(items)]
        filtered = [items[i] for i in valid_indices]

        if not filtered:
            if len(items) <= 5:
                log.info("  LLM filtered all %d items — accepting (small set)", len(items))
                return items
            log.warning("  LLM filtered all %d items — suspicious, keeping heuristic-filtered set", len(items))
            return items

        return filtered

    except Exception as e:
        log.warning("  LLM filter failed (%s), keeping heuristic-filtered set", e)
        return items
