"""수집 실행 계측(run metrics).

eBay API 한도 소진율·LLM 호출량·429 발생 빈도를 실행마다 기록해
이후 최적화의 측정 기준(baseline)으로 삼는다.

전역 변수를 남발하는 대신 :class:`RunMetrics` 수집기 객체 하나에 카운터를
모으고, `ebay.py`/`llm.py`는 인자로 주입받거나(테스트) 활성 수집기를
가져와서(프로덕션) 증가시킨다. `reset_metrics()`로 언제든 초기화할 수 있다.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from nikon_value.paths import RUN_METRICS_PATH

log = logging.getLogger(__name__)

# 롤링 보관 개수. storage.update_product_history와 같은 "최근 N개만 유지" 패턴.
MAX_RUN_METRICS = 200


def _utc_now_iso() -> str:
    """초 단위 UTC ISO 타임스탬프."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass
class RunMetrics:
    """한 번의 수집 실행에서 누적되는 카운터 묶음."""

    started_at: str = field(default_factory=_utc_now_iso)
    products_processed: int = 0
    products_failed: int = 0
    ebay_search_calls: int = 0
    ebay_http_requests: int = 0
    ebay_rate_limited: int = 0
    ebay_diagnostic_probes: int = 0
    llm_calls: int = 0
    llm_cache_hits: int = 0
    llm_cache_misses: int = 0
    max_price_expansions: int = 0
    duration_seconds: float = 0.0
    _monotonic_start: float = field(default_factory=time.monotonic, repr=False)

    # --- 카운터 증가 -------------------------------------------------
    def record_product(self, count: int = 1) -> None:
        self.products_processed += count

    def record_product_failure(self, count: int = 1) -> None:
        """제품 1개 수집이 예외로 실패했다(격리된 실패)."""
        self.products_failed += count

    def failure_rate(self) -> float:
        """처리한 제품 대비 실패 비율. 처리한 제품이 없으면 0."""
        if not self.products_processed:
            return 0.0
        return self.products_failed / self.products_processed

    def record_ebay_search(self, count: int = 1) -> None:
        """검색 1건(페이지네이션 전) 호출."""
        self.ebay_search_calls += count

    def record_http_request(self, count: int = 1) -> None:
        """실제 HTTP 요청 1건(페이지네이션·재시도 포함)."""
        self.ebay_http_requests += count

    def record_rate_limited(self, count: int = 1) -> None:
        self.ebay_rate_limited += count

    def record_diagnostic_probe(self, count: int = 1) -> None:
        """0건 제품 진단 프로브 1건. HTTP 요청 수에도 함께 반영된다."""
        self.ebay_diagnostic_probes += count

    def record_llm_call(self, count: int = 1) -> None:
        self.llm_calls += count

    def record_llm_cache_hits(self, count: int = 1) -> None:
        self.llm_cache_hits += count

    def record_llm_cache_misses(self, count: int = 1) -> None:
        self.llm_cache_misses += count

    def record_max_price_expansion(self, count: int = 1) -> None:
        self.max_price_expansions += count

    # --- 마무리 ------------------------------------------------------
    def finish(self) -> RunMetrics:
        """소요 시간을 확정한다. 여러 번 호출해도 안전하다."""
        self.duration_seconds = round(time.monotonic() - self._monotonic_start, 2)
        return self

    def to_dict(self) -> dict:
        """JSON 직렬화용 사전. 내부 필드(_monotonic_start)는 제외한다."""
        return {
            "started_at": self.started_at,
            "products_processed": self.products_processed,
            "products_failed": self.products_failed,
            "ebay_search_calls": self.ebay_search_calls,
            "ebay_http_requests": self.ebay_http_requests,
            "ebay_rate_limited": self.ebay_rate_limited,
            "ebay_diagnostic_probes": self.ebay_diagnostic_probes,
            "llm_calls": self.llm_calls,
            "llm_cache_hits": self.llm_cache_hits,
            "llm_cache_misses": self.llm_cache_misses,
            "max_price_expansions": self.max_price_expansions,
            "duration_seconds": self.duration_seconds,
        }

    def summary_line(self) -> str:
        """실행 종료 로그용 한 줄 요약."""
        return (
            f"Run metrics: {self.products_processed} products "
            f"({self.products_failed} failed), "
            f"{self.ebay_search_calls} eBay searches "
            f"({self.ebay_http_requests} HTTP requests, {self.ebay_rate_limited} rate limited), "
            f"{self.ebay_diagnostic_probes} diagnostic probes, "
            f"{self.llm_calls} LLM calls "
            f"({self.llm_cache_hits} cache hits / {self.llm_cache_misses} misses), "
            f"{self.max_price_expansions} max-price expansions, {self.duration_seconds:.1f}s"
        )


_active_metrics = RunMetrics()


def get_metrics() -> RunMetrics:
    """현재 활성 수집기를 반환합니다."""
    return _active_metrics


def set_metrics(metrics: RunMetrics) -> RunMetrics:
    """활성 수집기를 교체합니다(테스트에서 주입 용도)."""
    global _active_metrics
    _active_metrics = metrics
    return _active_metrics


def reset_metrics() -> RunMetrics:
    """활성 수집기를 새 것으로 초기화합니다."""
    return set_metrics(RunMetrics())


def resolve_metrics(metrics: RunMetrics | None) -> RunMetrics:
    """인자로 주입된 수집기가 없으면 활성 수집기를 사용합니다."""
    return metrics if metrics is not None else get_metrics()


def load_run_metrics(path: Path = RUN_METRICS_PATH) -> list[dict]:
    """기록된 실행 계측 목록을 오래된 순으로 로드합니다."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Run metrics file unreadable (%s), starting a new one", exc)
        return []
    runs = data.get("runs") if isinstance(data, dict) else data
    if not isinstance(runs, list):
        return []
    return [run for run in runs if isinstance(run, dict)]


def append_run_metrics(
    metrics: RunMetrics,
    path: Path = RUN_METRICS_PATH,
    limit: int = MAX_RUN_METRICS,
) -> dict:
    """실행 계측을 파일에 append하고 최근 `limit`회만 남깁니다."""
    metrics.finish()
    record = metrics.to_dict()

    runs = load_run_metrics(path)
    runs.append(record)
    runs = runs[-limit:]

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"runs": runs}, f, ensure_ascii=False, indent=1)
    return record
