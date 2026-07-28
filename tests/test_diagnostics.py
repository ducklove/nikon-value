"""0건 제품 자동 진단(프로브) 테스트.

실제 eBay를 호출하지 않는다. HTTP는 전부 가짜 세션으로 대체하고, 프로브가
어떤 파라미터를 만들었는지·판정이 무엇인지·예산 상한이 지켜지는지를 본다.
"""

from __future__ import annotations

import json

import pytest

from nikon_value import diagnostics
from nikon_value.diagnostics import (
    PROBE_COUNT,
    PROBE_MAX_PRODUCTS_PER_DAY,
    PROBE_MAX_PRODUCTS_PER_RUN,
    PROBE_SPECS,
    VERDICT_CONSTRAINT_SUSPECTS,
    VERDICT_INCOMPLETE,
    VERDICT_NO_LISTINGS,
    VERDICT_NOT_A_SEARCH_PROBLEM,
    ProbeResult,
    config_fingerprint,
    core_query,
    diagnose_empty_products,
    load_report,
    probe_interval_days,
    probe_product,
    select_probe_targets,
    strip_query_exclusions,
    summarize_probes,
)
from nikon_value.metrics import RunMetrics

PRODUCT = {
    "id": "nikon-fe10",
    "name_en": "Nikon FE10",
    "query": "Nikon FE10 body -lens -nikkor -filter",
    "category_id": "3323",
    "min_price": 20,
    "max_price": 150,
}


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def json(self):
        return self.payload


class _FakeSession:
    """프로브가 보낸 요청을 기록하고 미리 정해둔 응답을 돌려준다."""

    def __init__(self, totals: dict[str, int] | None = None, default: int = 0):
        self.totals = totals or {}
        self.default = default
        self.requests: list[dict] = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.requests.append({"url": url, "headers": headers, "params": params})
        # 프로브 이름은 params로 역추적할 수 없으므로 호출 순서로 매핑한다.
        spec = PROBE_SPECS[len(self.requests) - 1]
        total = self.totals.get(spec.name, self.default)
        items = [{"title": f"{spec.name} listing #{i}"} for i in range(min(total, 3))]
        return _FakeResponse({"total": total, "itemSummaries": items})


# --- 질의 단순화 --------------------------------------------------------


def test_strip_query_exclusions_drops_only_the_minus_tokens():
    assert strip_query_exclusions("Nikon FE10 body -lens -nikkor") == "Nikon FE10 body"
    assert strip_query_exclusions("Nikon Z9 body") == "Nikon Z9 body"


def test_core_query_drops_generic_nouns_and_optical_designators():
    assert core_query("Nikon AF Nikkor 28mm f/1.4D ED") == "Nikon AF Nikkor 28mm"
    assert core_query("Nikkorex Auto 35 camera -manual -case") == "Nikkorex Auto 35"
    assert core_query("Nikon AF-P 70-300mm f/4.5-6.3E ED VR") == "Nikon AF-P 70-300mm"


def test_core_query_never_returns_an_empty_string():
    # 모든 토큰이 일반 명사여도 원본으로 되돌아간다(빈 q를 eBay에 보내지 않는다).
    assert core_query("camera body lens") == "camera body lens"


# --- 프로브 요청 --------------------------------------------------------


def test_the_baseline_probe_sends_exactly_the_production_request():
    from nikon_value.ebay import build_search_params

    session = _FakeSession()
    probe_product("token", "https://browse", PRODUCT, metrics=RunMetrics(), session=session)

    production = build_search_params(
        PRODUCT["query"], PRODUCT["category_id"],
        float(PRODUCT["min_price"]), float(PRODUCT["max_price"]),
    )
    baseline = session.requests[0]["params"]
    assert baseline["q"] == production["q"]
    assert baseline["filter"] == production["filter"]
    assert baseline["category_ids"] == production["category_ids"]


def test_each_probe_relaxes_exactly_one_constraint():
    session = _FakeSession()
    probe_product("token", "https://browse", PRODUCT, metrics=RunMetrics(), session=session)

    by_name = {
        spec.name: request["params"]
        for spec, request in zip(PROBE_SPECS, session.requests, strict=True)
    }

    assert "conditionIds" in by_name["baseline"]["filter"]
    assert "conditionIds" not in by_name["no_condition"]["filter"]
    assert "deliveryCountry" not in by_name["no_delivery_country"]["filter"]
    assert "buyingOptions" not in by_name["no_buying_options"]["filter"]
    assert "price:" not in by_name["no_price_window"]["filter"]
    assert "category_ids" not in by_name["no_category"]
    assert "-lens" not in by_name["no_query_exclusions"]["q"]
    assert by_name["core_query_only"].get("filter") in (None, "")
    assert "category_ids" not in by_name["core_query_only"]


def test_probes_use_the_search_category_id_override_when_present():
    product = dict(PRODUCT, search_category_id="15230")
    session = _FakeSession()
    probe_product("token", "https://browse", product, metrics=RunMetrics(), session=session)

    assert session.requests[0]["params"]["category_ids"] == "15230"


def test_probes_never_paginate_and_are_counted_in_the_metrics():
    metrics = RunMetrics()
    session = _FakeSession(default=5000)

    probe_product("token", "https://browse", PRODUCT, metrics=metrics, session=session)

    assert len(session.requests) == PROBE_COUNT
    assert metrics.ebay_diagnostic_probes == PROBE_COUNT
    assert metrics.ebay_http_requests == PROBE_COUNT
    assert metrics.to_dict()["ebay_diagnostic_probes"] == PROBE_COUNT


def test_a_failing_probe_is_recorded_instead_of_raising():
    class _Boom:
        def get(self, *a, **kw):
            raise ConnectionError("network down")

    results = probe_product("token", "https://browse", PRODUCT, metrics=RunMetrics(), session=_Boom())

    assert len(results) == PROBE_COUNT
    assert all(result.total is None for result in results)
    assert all("ConnectionError" in (result.error or "") for result in results)


# --- 판정 --------------------------------------------------------------


def _results(**totals) -> list[ProbeResult]:
    return [
        ProbeResult(spec.name, spec.description_ko, "q", total=totals.get(spec.name, 0))
        for spec in PROBE_SPECS
    ]


def test_a_probe_sweep_that_finds_nothing_means_there_are_no_listings():
    assert summarize_probes(_results()) == (VERDICT_NO_LISTINGS, [])


def test_the_relaxed_constraint_that_finds_listings_becomes_the_suspect():
    verdict, suspects = summarize_probes(_results(no_delivery_country=12))
    assert verdict == VERDICT_CONSTRAINT_SUSPECTS
    assert suspects == ["no_delivery_country"]


def test_suspects_are_ordered_by_how_many_listings_they_uncovered():
    verdict, suspects = summarize_probes(
        _results(no_condition=3, core_query_only=90, no_price_window=20)
    )
    assert verdict == VERDICT_CONSTRAINT_SUSPECTS
    assert suspects == ["core_query_only", "no_price_window", "no_condition"]


def test_a_baseline_that_returns_listings_points_away_from_the_search():
    # count==0인데 검색은 매물을 돌려줬다면 원인은 필터·가격 추출 쪽이다.
    verdict, suspects = summarize_probes(_results(baseline=4, no_condition=9))
    assert verdict == VERDICT_NOT_A_SEARCH_PROBLEM
    assert suspects == []


def test_a_failed_baseline_is_never_reported_as_no_listings():
    results = _results()
    results[0].total = None
    results[0].error = "Timeout"
    assert summarize_probes(results) == (VERDICT_INCOMPLETE, [])


def test_a_failed_relaxation_probe_blocks_a_no_listings_verdict():
    results = _results()
    results[-1].total = None
    results[-1].error = "Timeout"
    assert summarize_probes(results)[0] == VERDICT_INCOMPLETE


# --- 대상 선정과 예산 ---------------------------------------------------


def test_probe_interval_backs_off_for_products_that_keep_finding_nothing():
    assert probe_interval_days(0) == diagnostics.PROBE_BASE_INTERVAL_DAYS
    assert probe_interval_days(1) == diagnostics.PROBE_BASE_INTERVAL_DAYS * 2
    assert probe_interval_days(99) == diagnostics.PROBE_MAX_INTERVAL_DAYS


def _report_entry(**overrides) -> dict:
    entry = {
        "last_probed": "2026-07-01",
        "inconclusive_streak": 0,
        "config_fingerprint": config_fingerprint(PRODUCT),
    }
    entry.update(overrides)
    return entry


def test_a_never_probed_product_is_always_selected():
    targets = select_probe_targets([PRODUCT], {"products": {}}, "2026-07-28")
    assert [t["id"] for t in targets] == ["nikon-fe10"]


def test_a_recently_probed_product_is_skipped_until_the_interval_passes():
    report = {"products": {"nikon-fe10": _report_entry(last_probed="2026-07-27")}}
    assert select_probe_targets([PRODUCT], report, "2026-07-28") == []

    report = {"products": {"nikon-fe10": _report_entry(last_probed="2026-07-01")}}
    assert len(select_probe_targets([PRODUCT], report, "2026-07-28")) == 1


def test_a_museum_grade_product_backs_off_further_after_each_empty_sweep():
    # 연속 3회 "매물 없음" → 간격이 112일로 늘어 14일 뒤에는 대상이 아니다.
    report = {"products": {"nikon-fe10": _report_entry(inconclusive_streak=3)}}
    assert select_probe_targets([PRODUCT], report, "2026-07-28") == []


def test_a_config_change_forces_an_immediate_reprobe():
    report = {
        "products": {
            "nikon-fe10": _report_entry(
                last_probed="2026-07-28", inconclusive_streak=5,
                config_fingerprint="stale-fingerprint",
            )
        }
    }
    targets = select_probe_targets([PRODUCT], report, "2026-07-28")
    assert [t["id"] for t in targets] == ["nikon-fe10"]


def test_config_fingerprint_tracks_every_field_that_shapes_the_search():
    base = config_fingerprint(PRODUCT)
    assert config_fingerprint(dict(PRODUCT, query="Nikon FE10")) != base
    assert config_fingerprint(dict(PRODUCT, max_price=400)) != base
    assert config_fingerprint(dict(PRODUCT, search_category_id="15230")) != base
    assert config_fingerprint(dict(PRODUCT, exclude_title_patterns=["kit"])) != base
    # 표시용 필드는 검색에 영향이 없으므로 지문을 흔들지 않는다.
    assert config_fingerprint(dict(PRODUCT, name_ko="니콘 FE10")) == base


def test_selection_respects_the_per_run_cap():
    candidates = [dict(PRODUCT, id=f"product-{i}") for i in range(40)]
    targets = select_probe_targets(candidates, {"products": {}}, "2026-07-28", daily_limit=100)
    assert len(targets) == PROBE_MAX_PRODUCTS_PER_RUN


def test_selection_respects_the_daily_cap_across_runs():
    already = {
        f"product-{i}": _report_entry(last_probed="2026-07-28")
        for i in range(PROBE_MAX_PRODUCTS_PER_DAY)
    }
    candidates = [dict(PRODUCT, id=f"product-{i}") for i in range(40)]

    assert select_probe_targets(candidates, {"products": already}, "2026-07-28") == []


def test_products_already_probed_today_are_not_probed_again():
    report = {"products": {"nikon-fe10": _report_entry(last_probed="2026-07-28")}}
    assert select_probe_targets([PRODUCT], report, "2026-07-28") == []


# --- 리포트 누적 --------------------------------------------------------


def test_diagnose_writes_a_report_a_human_and_the_next_run_can_read(tmp_path):
    path = tmp_path / "empty-product-report.json"
    session = _FakeSession(totals={"no_delivery_country": 17})

    diagnose_empty_products(
        "token", "https://browse", [PRODUCT], "2026-07-28",
        {"nikon-fe10": 148}, metrics=RunMetrics(), path=path, session=session,
    )

    entry = json.loads(path.read_text(encoding="utf-8"))["products"]["nikon-fe10"]
    assert entry["verdict"] == VERDICT_CONSTRAINT_SUSPECTS
    assert entry["suspects"] == ["no_delivery_country"]
    assert entry["zero_result_streak"] == 148
    assert entry["probe_count"] == 1
    assert entry["inconclusive_streak"] == 0
    assert len(entry["probes"]) == PROBE_COUNT
    assert entry["verdict_ko"]
    # 증거로 쓸 타이틀이 함께 남는다.
    assert entry["probes"][2]["sample_titles"]


def test_repeated_empty_sweeps_accumulate_an_inconclusive_streak(tmp_path):
    path = tmp_path / "report.json"

    for day in ("2026-01-01", "2026-03-01", "2026-09-01"):
        diagnose_empty_products(
            "token", "https://browse", [PRODUCT], day, {"nikon-fe10": 300},
            metrics=RunMetrics(), path=path, session=_FakeSession(),
        )

    entry = load_report(path)["products"]["nikon-fe10"]
    assert entry["verdict"] == VERDICT_NO_LISTINGS
    assert entry["probe_count"] == 3
    assert entry["inconclusive_streak"] == 3
    assert entry["next_probe_after_days"] == diagnostics.PROBE_MAX_INTERVAL_DAYS
    assert [h["date"] for h in entry["history"]] == ["2026-01-01", "2026-03-01", "2026-09-01"]


def test_a_streak_resets_as_soon_as_a_probe_finds_something(tmp_path):
    path = tmp_path / "report.json"
    diagnose_empty_products(
        "token", "https://browse", [PRODUCT], "2026-01-01", {}, metrics=RunMetrics(),
        path=path, session=_FakeSession(),
    )
    diagnose_empty_products(
        "token", "https://browse", [PRODUCT], "2026-03-01", {}, metrics=RunMetrics(),
        path=path, session=_FakeSession(totals={"no_condition": 5}),
    )

    entry = load_report(path)["products"]["nikon-fe10"]
    assert entry["inconclusive_streak"] == 0
    assert entry["suspects"] == ["no_condition"]


def test_nothing_is_written_when_no_candidate_is_due(tmp_path):
    path = tmp_path / "report.json"
    diagnose_empty_products(
        "token", "https://browse", [PRODUCT], "2026-01-01", {}, metrics=RunMetrics(),
        path=path, session=_FakeSession(),
    )
    mtime = path.stat().st_mtime_ns

    diagnose_empty_products(
        "token", "https://browse", [PRODUCT], "2026-01-02", {}, metrics=RunMetrics(),
        path=path, session=_FakeSession(),
    )
    assert path.stat().st_mtime_ns == mtime


def test_no_candidates_means_no_api_calls_at_all(tmp_path):
    session = _FakeSession()
    diagnose_empty_products(
        "token", "https://browse", [], "2026-01-01", {}, metrics=RunMetrics(),
        path=tmp_path / "report.json", session=session,
    )
    assert session.requests == []


def test_load_report_tolerates_a_corrupt_file(tmp_path):
    path = tmp_path / "report.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_report(path) == {"updated": None, "products": {}}


@pytest.mark.parametrize("spec", PROBE_SPECS)
def test_every_probe_is_documented_in_korean(spec):
    assert spec.description_ko.strip()
