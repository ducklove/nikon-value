"""0건 제품 자동 진단(프로브).

수집이 어떤 제품에서 0건을 반환하면, 그 제품에 한해 **검색 제약을 하나씩만
푼 프로브**를 날려 어느 제약이 매물을 전부 걷어냈는지 기록한다. 결과는
`data/empty-product-report.json`에 누적되어 다음 실행과 사람이 함께 읽는다.

왜 이런 형태인가
----------------
`fetch.py`의 `count`는 규칙 필터·LLM 필터를 모두 통과한 뒤 가격이 있는 매물
수다. 그런데 두 필터 모두 "전부 걸러내면 원본을 유지"하는 폴백이 있으므로
(``filters.filter_items_with_rules``, ``llm._keep_heuristic_set``) **비어 있지
않은 입력이 비어 있는 출력이 되는 경로는 없다.** 따라서 `count == 0`은 사실상
eBay 검색이 0건을 반환했다는 뜻이다. 유일한 예외는 "매물은 있는데 전부 가격이
없는" 경우(``collect_prices``가 다 떨어뜨림)이므로, baseline 프로브가 정규
수집과 문자 그대로 같은 요청을 날려 그 예외까지 구분해 준다.

예산 규율
---------
- 후보는 연속 0건이 :data:`PROBE_MIN_ZERO_STREAK`회 이상 쌓인 제품뿐이다
  (하루치 일시적 0건에 8회를 쓰지 않는다).
- 같은 제품은 :data:`PROBE_BASE_INTERVAL_DAYS`마다 한 번만, 그리고 "어느
  프로브도 매물을 못 찾은"(= 진짜 매물이 없는 박물관급) 판정이 반복될수록
  간격을 2배씩 늘려 :data:`PROBE_MAX_INTERVAL_DAYS`까지 물러난다.
- 설정(query·category·가격창)이 바뀌면 간격과 무관하게 즉시 재프로브한다.
  설정 수정이 다음 수집에서 곧바로 검증되는 통로다.
- 한 실행 :data:`PROBE_MAX_PRODUCTS_PER_RUN`개, 하루 :data:`PROBE_MAX_PRODUCTS_PER_DAY`개
  제품이 상한이다. 프로브는 페이지네이션 없이 1요청씩만 쓴다.

프로브는 진단 전용이다. 결과는 catalog.json에도, 제품 히스토리에도 들어가지
않는다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import requests

from nikon_value.ebay import (
    DEFAULT_SEARCH_CONSTRAINTS,
    UNCONSTRAINED_SEARCH,
    SearchConstraints,
    build_search_headers,
    build_search_params,
    relax,
)
from nikon_value.metrics import RunMetrics, resolve_metrics
from nikon_value.paths import EMPTY_PRODUCT_REPORT_PATH
from nikon_value.storage import ZERO_RESULT_STREAK_THRESHOLD

log = logging.getLogger(__name__)

# --- 예산 상한 ---------------------------------------------------------

# 이만큼 연속 0건이 쌓인 제품만 프로브한다(적응형 상한 확장을 멈추는 기준과 동일).
PROBE_MIN_ZERO_STREAK = ZERO_RESULT_STREAK_THRESHOLD
PROBE_BASE_INTERVAL_DAYS = 14
PROBE_MAX_INTERVAL_DAYS = 112  # 14 * 2^3
PROBE_MAX_PRODUCTS_PER_RUN = 8
PROBE_MAX_PRODUCTS_PER_DAY = 8
# 프로브는 건수만 보면 되므로 최소한만 받는다(증거용 타이틀 몇 줄).
PROBE_LIMIT = 3
PROBE_SAMPLE_TITLES = 3
PROBE_TIMEOUT_SECONDS = 30
# 제품당 보관할 과거 판정 개수.
MAX_VERDICT_HISTORY = 10

# `q`를 단순화할 때 떨어뜨리는 토큰. 일반 명사와 광학 사양 표기는 eBay 타이틀에
# 없을 수 있는데도 공백(=AND)으로 묶여 매칭을 좁힌다. 모델을 식별하는 토큰
# (브랜드·마운트·초점거리)은 남긴다.
GENERIC_QUERY_TOKENS = frozenset({
    # 일반 명사
    "body", "camera", "lens", "dslr", "slr", "film", "rangefinder", "kit",
    # 광학·기능 표기
    "ed", "if", "if-ed", "ed-if", "vr", "vrii", "vr2", "pf", "fl", "asph",
    "aspherical", "swm", "n", "nano",
})


# --- 프로브 정의 -------------------------------------------------------


@dataclass(frozen=True)
class ProbeSpec:
    """프로브 1종: 어떤 제약을 풀고 q를 어떻게 손볼지."""

    name: str
    description_ko: str
    constraints: SearchConstraints = DEFAULT_SEARCH_CONSTRAINTS
    query_mode: str = "as_is"  # as_is | no_exclusions | core


def strip_query_exclusions(query: str) -> str:
    """`-토큰` 제외어를 모두 뺀 질의를 만듭니다."""
    kept = [token for token in query.split() if not token.startswith("-")]
    return " ".join(kept)


def core_query(query: str) -> str:
    """제외어·조리개 표기·일반 명사를 뺀 최소 질의를 만듭니다.

    eBay Browse API의 `q`는 공백을 AND로 해석하므로 토큰이 늘수록 매칭이
    좁아진다. "모델명만 남기면 잡히는가"를 확인하는 프로브다.
    """
    kept = []
    for token in strip_query_exclusions(query).split():
        lowered = token.lower()
        if lowered.startswith("f/"):
            continue
        if lowered in GENERIC_QUERY_TOKENS:
            continue
        kept.append(token)
    return " ".join(kept) or strip_query_exclusions(query) or query


def apply_query_mode(query: str, mode: str) -> str:
    """프로브의 `query_mode`에 따라 질의를 변형합니다."""
    if mode == "no_exclusions":
        return strip_query_exclusions(query) or query
    if mode == "core":
        return core_query(query)
    return query


PROBE_SPECS: tuple[ProbeSpec, ...] = (
    ProbeSpec(
        "baseline",
        "정규 수집과 완전히 동일한 요청(기준선)",
    ),
    ProbeSpec(
        "no_condition",
        "conditionIds 제거 — USED(3000) 외 상태(Open box·Refurbished·Very Good 등) 포함",
        relax(DEFAULT_SEARCH_CONSTRAINTS, condition_ids=()),
    ),
    ProbeSpec(
        "no_delivery_country",
        "deliveryCountry:KR 제거 — 한국 배송을 하지 않는 셀러 포함",
        relax(DEFAULT_SEARCH_CONSTRAINTS, delivery_country=None),
    ),
    ProbeSpec(
        "no_buying_options",
        "buyingOptions 제거 — 경매 매물 포함",
        relax(DEFAULT_SEARCH_CONSTRAINTS, buying_options=()),
    ),
    ProbeSpec(
        "no_price_window",
        "가격창 제거 — min_price..max_price 밖의 매물 포함",
        relax(DEFAULT_SEARCH_CONSTRAINTS, price_window=False),
    ),
    ProbeSpec(
        "no_category",
        "category_ids 제거 — 다른 eBay 카테고리에 올라온 매물 포함",
        relax(DEFAULT_SEARCH_CONSTRAINTS, use_category=False),
    ),
    ProbeSpec(
        "no_query_exclusions",
        "q의 `-제외어` 제거 — 제외어가 과했는지 확인",
        DEFAULT_SEARCH_CONSTRAINTS,
        query_mode="no_exclusions",
    ),
    ProbeSpec(
        "core_query_only",
        "q를 모델명만 남기고 모든 제약 해제 — eBay에 매물 자체가 있는지 확인",
        UNCONSTRAINED_SEARCH,
        query_mode="core",
    ),
)

PROBE_COUNT = len(PROBE_SPECS)


# --- 판정 --------------------------------------------------------------

VERDICT_NO_LISTINGS = "no_listings"
VERDICT_NOT_A_SEARCH_PROBLEM = "not_a_search_problem"
VERDICT_CONSTRAINT_SUSPECTS = "constraint_suspects"
VERDICT_INCOMPLETE = "incomplete"

VERDICT_LABELS_KO = {
    VERDICT_NO_LISTINGS: "어떤 제약을 풀어도 0건 — eBay에 매물이 없다(진짜 희귀)",
    VERDICT_NOT_A_SEARCH_PROBLEM: "검색은 매물을 반환했다 — 필터/가격 추출 쪽을 봐야 한다",
    VERDICT_CONSTRAINT_SUSPECTS: "특정 제약을 풀자 매물이 나타났다",
    VERDICT_INCOMPLETE: "프로브가 실패해 판정할 수 없다",
}


@dataclass
class ProbeResult:
    """프로브 1건의 결과."""

    name: str
    description_ko: str
    query: str
    total: int | None = None
    sample_titles: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description_ko": self.description_ko,
            "query": self.query,
            "total": self.total,
            "sample_titles": self.sample_titles,
            "error": self.error,
        }


def summarize_probes(results: list[ProbeResult]) -> tuple[str, list[str]]:
    """프로브 결과에서 판정과 용의자 목록을 뽑습니다.

    용의자는 "이 제약만 풀었더니 매물이 나타난" 프로브다. 매물 수가 많은
    순으로 정렬해 가장 유력한 원인이 앞에 오게 한다.
    """
    by_name = {result.name: result for result in results}
    baseline = by_name.get("baseline")

    if baseline is not None and baseline.total is None:
        return VERDICT_INCOMPLETE, []
    if baseline is not None and baseline.total:
        return VERDICT_NOT_A_SEARCH_PROBLEM, []

    suspects = sorted(
        (r for r in results if r.name != "baseline" and r.total),
        key=lambda r: (-(r.total or 0), r.name),
    )
    if suspects:
        return VERDICT_CONSTRAINT_SUSPECTS, [r.name for r in suspects]

    if any(r.total is None for r in results):
        return VERDICT_INCOMPLETE, []
    return VERDICT_NO_LISTINGS, []


# --- 프로브 실행 -------------------------------------------------------


def _extract_titles(payload: dict) -> list[str]:
    items = payload.get("itemSummaries") or []
    titles = [item.get("title", "") for item in items if isinstance(item, dict)]
    return [title for title in titles if title][:PROBE_SAMPLE_TITLES]


def _probe_total(payload: dict) -> int:
    """응답에서 매물 수를 읽습니다. `total`이 없으면 받은 건수로 대체."""
    total = payload.get("total")
    if isinstance(total, int):
        return total
    return len(payload.get("itemSummaries") or [])


def run_probe(
    token: str,
    browse_url: str,
    product: dict,
    spec: ProbeSpec,
    metrics: RunMetrics | None = None,
    session: object | None = None,
) -> ProbeResult:
    """프로브 1건을 실행합니다. 실패해도 예외를 밖으로 내보내지 않는다."""
    run_metrics = resolve_metrics(metrics)
    query = apply_query_mode(product["query"], spec.query_mode)
    result = ProbeResult(name=spec.name, description_ko=spec.description_ko, query=query)

    params = build_search_params(
        query,
        product.get("search_category_id", product["category_id"]),
        float(product["min_price"]),
        float(product["max_price"]),
        constraints=spec.constraints,
        limit=PROBE_LIMIT,
    )

    getter = session.get if session is not None else requests.get
    try:
        resp = getter(
            browse_url,
            headers=build_search_headers(token),
            params=params,
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        run_metrics.record_http_request()
        run_metrics.record_diagnostic_probe()
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # 진단이 수집을 죽이면 안 된다
        result.error = f"{type(exc).__name__}: {exc}"
        log.warning("  Probe %s failed for %s (%s)", spec.name, product["id"], result.error)
        return result

    result.total = _probe_total(payload)
    result.sample_titles = _extract_titles(payload)
    return result


def probe_product(
    token: str,
    browse_url: str,
    product: dict,
    metrics: RunMetrics | None = None,
    session: object | None = None,
) -> list[ProbeResult]:
    """제품 1개에 대해 모든 프로브를 실행합니다."""
    return [
        run_probe(token, browse_url, product, spec, metrics=metrics, session=session)
        for spec in PROBE_SPECS
    ]


# --- 대상 선정 ---------------------------------------------------------


def config_fingerprint(product: dict) -> str:
    """검색 결과를 좌우하는 설정 필드의 지문.

    이 값이 바뀌면(질의·카테고리·가격창·제외 패턴 수정) 간격과 무관하게
    다시 프로브해 수정이 효과가 있었는지 다음 수집에서 바로 확인한다.
    """
    payload = json.dumps(
        {
            "query": product.get("query"),
            "category_id": product.get("category_id"),
            "search_category_id": product.get("search_category_id"),
            "min_price": product.get("min_price"),
            "max_price": product.get("max_price"),
            "exclude_title_patterns": product.get("exclude_title_patterns"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def probe_interval_days(inconclusive_streak: int) -> int:
    """"진짜 매물 없음" 판정이 반복될수록 프로브 간격을 2배씩 늘립니다."""
    interval = PROBE_BASE_INTERVAL_DAYS * (2 ** max(inconclusive_streak, 0))
    return min(interval, PROBE_MAX_INTERVAL_DAYS)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def days_until_due(entry: dict | None, today: date) -> int:
    """다음 프로브까지 남은 일수. 0 이하면 지금 프로브해도 된다.

    설정이 바뀐 제품과 한 번도 프로브하지 않은 제품은 항상 즉시 대상이다.
    """
    if not entry:
        return -PROBE_MAX_INTERVAL_DAYS  # 미프로브 제품을 가장 앞에 세운다
    last = _parse_date(entry.get("last_probed"))
    if last is None:
        return -PROBE_MAX_INTERVAL_DAYS
    interval = probe_interval_days(int(entry.get("inconclusive_streak") or 0))
    return (last + timedelta(days=interval) - today).days


def select_probe_targets(
    candidates: list[dict],
    report: dict,
    today: str,
    limit: int = PROBE_MAX_PRODUCTS_PER_RUN,
    daily_limit: int = PROBE_MAX_PRODUCTS_PER_DAY,
) -> list[dict]:
    """이번 실행에서 프로브할 제품을 상한 안에서 고릅니다.

    우선순위는 (1) 설정이 바뀐 제품, (2) 가장 오래 밀린 제품 순.
    """
    today_date = _parse_date(today) or date.today()
    products = report.get("products") or {}

    def changed_since_last_probe(product: dict) -> bool:
        entry = products.get(product["id"])
        return bool(entry) and entry.get("config_fingerprint") != config_fingerprint(product)

    changed_ids = {p["id"] for p in candidates if changed_since_last_probe(p)}
    # 오늘 이미 프로브한 제품은 다시 세지 않는다. 단 설정이 바뀐 제품은 예외로,
    # 수정이 효과가 있었는지 바로 다음 수집에서 확인할 수 있어야 한다.
    probed_today = {
        pid for pid, entry in products.items()
        if isinstance(entry, dict) and entry.get("last_probed") == today and pid not in changed_ids
    }
    remaining_today = max(daily_limit - len(probed_today), 0)
    if remaining_today <= 0:
        return []

    scored = []
    for product in candidates:
        pid = product["id"]
        if pid in probed_today:
            continue
        changed = pid in changed_ids
        due = days_until_due(products.get(pid), today_date)
        if not changed and due > 0:
            continue
        scored.append((0 if changed else 1, due, pid, product))

    scored.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[3] for row in scored[: min(limit, remaining_today)]]


# --- 리포트 저장 -------------------------------------------------------


def load_report(path=EMPTY_PRODUCT_REPORT_PATH) -> dict:
    """누적 리포트를 로드합니다. 없거나 깨졌으면 빈 리포트."""
    if not path.exists():
        return {"updated": None, "products": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Empty-product report unreadable (%s), starting a new one", exc)
        return {"updated": None, "products": {}}
    if not isinstance(data, dict) or not isinstance(data.get("products"), dict):
        return {"updated": None, "products": {}}
    return data


def save_report(report: dict, path=EMPTY_PRODUCT_REPORT_PATH) -> None:
    """리포트를 저장합니다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)


def record_probe_results(
    report: dict,
    product: dict,
    results: list[ProbeResult],
    today: str,
    zero_streak: int,
) -> dict:
    """프로브 결과를 리포트에 반영하고 갱신된 제품 항목을 반환합니다."""
    verdict, suspects = summarize_probes(results)
    products = report.setdefault("products", {})
    previous = products.get(product["id"]) or {}

    if verdict == VERDICT_NO_LISTINGS:
        inconclusive_streak = int(previous.get("inconclusive_streak") or 0) + 1
    else:
        inconclusive_streak = 0

    history = list(previous.get("history") or [])
    history.append({"date": today, "verdict": verdict, "suspects": suspects})

    entry = {
        "product_id": product["id"],
        "name_en": product.get("name_en", ""),
        "query": product["query"],
        "category_id": product.get("search_category_id", product.get("category_id")),
        "min_price": product.get("min_price"),
        "max_price": product.get("max_price"),
        "config_fingerprint": config_fingerprint(product),
        "zero_result_streak": zero_streak,
        "first_probed": previous.get("first_probed") or today,
        "last_probed": today,
        "probe_count": int(previous.get("probe_count") or 0) + 1,
        "inconclusive_streak": inconclusive_streak,
        "next_probe_after_days": probe_interval_days(inconclusive_streak),
        "verdict": verdict,
        "verdict_ko": VERDICT_LABELS_KO[verdict],
        "suspects": suspects,
        "probes": [result.to_dict() for result in results],
        "history": history[-MAX_VERDICT_HISTORY:],
    }
    products[product["id"]] = entry
    report["updated"] = today
    return entry


# --- 오케스트레이션 ----------------------------------------------------


def diagnose_empty_products(
    token: str,
    browse_url: str,
    candidates: list[dict],
    today: str,
    zero_streaks: dict[str, int],
    metrics: RunMetrics | None = None,
    path=EMPTY_PRODUCT_REPORT_PATH,
    session: object | None = None,
) -> dict:
    """0건 제품들을 상한 안에서 진단하고 리포트를 갱신합니다.

    반환값은 갱신된 리포트다. 프로브를 한 건도 돌리지 않았으면 파일을 쓰지
    않는다(리포트가 매 실행마다 무의미하게 커밋되는 것을 막는다).
    """
    if not candidates:
        return load_report(path)

    report = load_report(path)
    targets = select_probe_targets(candidates, report, today)
    if not targets:
        log.info("Empty-product diagnostics: %d candidates, none due", len(candidates))
        return report

    log.info(
        "Empty-product diagnostics: probing %d/%d candidates (%d requests)",
        len(targets), len(candidates), len(targets) * PROBE_COUNT,
    )
    for product in targets:
        results = probe_product(token, browse_url, product, metrics=metrics, session=session)
        entry = record_probe_results(
            report, product, results, today, zero_streaks.get(product["id"], 0)
        )
        log.info(
            "  %s → %s%s",
            product["id"],
            entry["verdict"],
            f" ({', '.join(entry['suspects'])})" if entry["suspects"] else "",
        )

    save_report(report, path)
    log.info("Saved %s", path)
    return report
