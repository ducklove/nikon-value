"""LLM 판정 캐시 ({제품ID + 타이틀 해시} → 통과 여부).

매물 타이틀은 실행 간 대부분 동일하므로 매번 LLM에 되묻는 것은 낭비다.
판정 결과를 파일에 지속시키고 미캐시 타이틀만 LLM에 보낸다.

캐시가 무한히 커지지 않도록 제품당 상한을 두고, 상한을 넘으면 최근 사용
시각(LRU) 기준으로 오래된 항목부터 버린다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

from nikon_value.paths import LLM_CACHE_PATH

log = logging.getLogger(__name__)

CACHE_VERSION = 1
MAX_ENTRIES_PER_PRODUCT = 2000


def title_key(title: str) -> str:
    """타이틀을 캐시 키(짧은 해시)로 변환합니다. 공백·대소문자 차이는 무시."""
    normalized = " ".join((title or "").split()).lower()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


class LlmDecisionCache:
    """제품별 타이틀 판정 캐시. 항목은 `[통과여부(0/1), 최근사용 epoch]`."""

    def __init__(
        self,
        products: dict[str, dict[str, list]] | None = None,
        path: Path = LLM_CACHE_PATH,
        max_entries_per_product: int = MAX_ENTRIES_PER_PRODUCT,
    ):
        self.products: dict[str, dict[str, list]] = products or {}
        self.path = path
        self.max_entries_per_product = max_entries_per_product
        self.dirty = False

    # --- 로드/저장 ---------------------------------------------------
    @classmethod
    def load(
        cls,
        path: Path = LLM_CACHE_PATH,
        max_entries_per_product: int = MAX_ENTRIES_PER_PRODUCT,
    ) -> LlmDecisionCache:
        """캐시 파일을 로드합니다. 없거나 깨졌으면 빈 캐시로 시작합니다."""
        products: dict[str, dict[str, list]] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                raw = data.get("products", {}) if isinstance(data, dict) else {}
                for product_id, entries in raw.items():
                    if isinstance(entries, dict):
                        products[product_id] = {
                            key: list(value)
                            for key, value in entries.items()
                            if isinstance(value, list) and len(value) >= 2
                        }
            except (OSError, json.JSONDecodeError, AttributeError) as exc:
                log.warning("LLM cache unreadable (%s), starting empty", exc)
                products = {}
        return cls(products, path=path, max_entries_per_product=max_entries_per_product)

    def save(self, path: Path | None = None) -> Path:
        """정리 후 캐시를 저장합니다. 수집 종료 시 한 번만 호출한다."""
        target = path or self.path
        self.prune()
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(
                {"version": CACHE_VERSION, "products": self.products},
                f,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        self.dirty = False
        return target

    # --- 조회/기록 ---------------------------------------------------
    def lookup(self, product_id: str, title: str, now: float | None = None) -> bool | None:
        """캐시된 판정을 반환합니다. 미캐시면 None. 히트 시 사용 시각을 갱신."""
        entry = self.products.get(product_id, {}).get(title_key(title))
        if entry is None:
            return None
        entry[1] = int(now if now is not None else time.time())
        self.dirty = True
        return bool(entry[0])

    def record(self, product_id: str, title: str, keep: bool, now: float | None = None) -> None:
        """판정 결과를 캐시에 기록합니다."""
        entries = self.products.setdefault(product_id, {})
        entries[title_key(title)] = [1 if keep else 0, int(now if now is not None else time.time())]
        self.dirty = True

    # --- 정리 --------------------------------------------------------
    def prune(self) -> int:
        """제품당 상한을 넘는 항목을 최근 사용 순(LRU)으로 잘라냅니다."""
        removed = 0
        for product_id, entries in list(self.products.items()):
            if not entries:
                del self.products[product_id]
                continue
            if len(entries) <= self.max_entries_per_product:
                continue
            ordered = sorted(entries.items(), key=lambda kv: kv[1][1], reverse=True)
            keep = dict(ordered[: self.max_entries_per_product])
            removed += len(entries) - len(keep)
            self.products[product_id] = keep
        if removed:
            log.info("LLM cache pruned %d stale entries", removed)
        return removed

    def entry_count(self) -> int:
        """전체 캐시 항목 수."""
        return sum(len(entries) for entries in self.products.values())
