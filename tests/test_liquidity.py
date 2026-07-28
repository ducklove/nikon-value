"""유동성 지표(nikon_value.sitegen.liquidity)와 그 렌더링 단위 테스트.

경계 조건이 핵심이다: 표본 부족, 전부 0건, 데이터 없음, 중복 날짜, 깨진 항목.
"이 정도 표본으로는 주장하지 않는다"는 규율이 코드에 남아 있는지 검증한다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from nikon_value.sitegen.components import build_liquidity_section
from nikon_value.sitegen.liquidity import (
    GRADE_ABUNDANT,
    GRADE_NORMAL,
    GRADE_SCARCE,
    GRADE_VERY_SCARCE,
    LIQUIDITY_WINDOW_DAYS,
    MIN_LIQUIDITY_POINTS,
    POSITION_FEW,
    POSITION_MANY,
    POSITION_NORMAL,
    build_card_scarcity,
    compute_liquidity,
    grade_liquidity,
)

START = date(2026, 1, 1)


def _history(counts: list[int | None], *, start: date = START) -> list[dict[str, Any]]:
    """counts를 하루 간격 히스토리로 만든다. None은 count 키가 없는 날."""
    entries = []
    for offset, count in enumerate(counts):
        entry: dict[str, Any] = {'date': (start + timedelta(days=offset)).isoformat()}
        if count is not None:
            entry['count'] = count
        entries.append(entry)
    return entries


# --------------------------------------------------------------------------- #
# 표본 부족 / 데이터 없음
# --------------------------------------------------------------------------- #


def test_compute_liquidity_returns_none_without_any_history() -> None:
    assert compute_liquidity([]) is None
    assert compute_liquidity(None) is None  # type: ignore[arg-type]


def test_compute_liquidity_returns_none_just_below_the_minimum_sample() -> None:
    """is_at_yearly_low()와 같은 규율 — 표본이 모자라면 아무 주장도 하지 않는다."""
    assert compute_liquidity(_history([5] * (MIN_LIQUIDITY_POINTS - 1))) is None


def test_compute_liquidity_computes_exactly_at_the_minimum_sample() -> None:
    result = compute_liquidity(_history([5] * MIN_LIQUIDITY_POINTS))

    assert result is not None
    assert result['days'] == MIN_LIQUIDITY_POINTS


def test_compute_liquidity_ignores_entries_outside_the_window() -> None:
    """창 밖 데이터가 아무리 많아도 창 안 관측일이 기준이다."""
    old = _history([50] * 200, start=date(2020, 1, 1))
    recent = _history([1] * 10, start=date(2026, 6, 1))

    assert compute_liquidity(old + recent) is None


def test_compute_liquidity_counts_only_the_trailing_window_days() -> None:
    result = compute_liquidity(_history([0] * 200 + [10] * 90))

    assert result is not None
    assert result['days'] == LIQUIDITY_WINDOW_DAYS
    assert result['avg_count'] == 10.0
    assert result['zero_days'] == 0


def test_compute_liquidity_anchors_the_window_to_the_last_observation_not_today() -> None:
    """수집이 멈춰도 지표가 조용히 0으로 희석되면 안 된다."""
    result = compute_liquidity(_history([4] * 90, start=date(2020, 1, 1)))

    assert result is not None
    assert result['days'] == 90
    assert result['avg_count'] == 4.0


def test_compute_liquidity_returns_none_for_an_unparsable_latest_date() -> None:
    assert compute_liquidity([{'date': 'not-a-date', 'count': 3}] * 40) is None


# --------------------------------------------------------------------------- #
# 전부 0건 / 부분 0건
# --------------------------------------------------------------------------- #


def test_compute_liquidity_of_a_never_listed_product() -> None:
    result = compute_liquidity(_history([0] * 90))

    assert result is not None
    assert result['avg_count'] == 0.0
    assert result['zero_days'] == 90
    assert result['zero_ratio'] == 1.0
    assert result['max_zero_streak'] == 90
    assert result['grade'] == GRADE_VERY_SCARCE
    # 분포 자체가 없으므로 "상대 위치"를 주장하지 않는다.
    assert result['position'] is None


def test_max_zero_streak_measures_the_longest_drought_not_the_total() -> None:
    """같은 0건 비율이라도 흩어진 하루와 연속 공백은 구매자에게 다른 의미다."""
    scattered = compute_liquidity(_history(([0, 3] * 10) + [3] * 70))
    clustered = compute_liquidity(_history([0] * 10 + [3] * 80))

    assert scattered is not None and clustered is not None
    assert scattered['zero_days'] == clustered['zero_days'] == 10
    assert scattered['max_zero_streak'] == 1
    assert clustered['max_zero_streak'] == 10


def test_missing_or_broken_count_values_are_treated_as_zero_listings() -> None:
    history = _history([None] * 45 + [2] * 45)
    history[0]['count'] = 'oops'
    history[1]['count'] = None
    history[2]['count'] = -7  # 음수는 있을 수 없지만 0으로 눌러 둔다

    result = compute_liquidity(history)

    assert result is not None
    assert result['zero_days'] == 45
    assert result['min_count'] == 0


def test_non_dict_and_dateless_entries_are_dropped() -> None:
    history: list[Any] = _history([3] * 40)
    history.extend(['garbage', 42, None, {'count': 9}, {'date': '', 'count': 9}])

    result = compute_liquidity(history)

    assert result is not None
    assert result['days'] == 40


def test_duplicate_dates_are_collapsed_to_the_last_observation() -> None:
    """실데이터에 같은 날짜가 두 번 들어간 제품이 20개 있다(수집 재실행)."""
    history = _history([2] * 40)
    history.append({'date': history[-1]['date'], 'count': 100})

    result = compute_liquidity(history)

    assert result is not None
    assert result['days'] == 40
    assert result['current'] == 100
    assert result['max_count'] == 100


def test_history_is_sorted_before_the_window_is_applied() -> None:
    ordered = _history([1] * 40)
    shuffled = list(reversed(ordered))

    assert compute_liquidity(shuffled) == compute_liquidity(ordered)


# --------------------------------------------------------------------------- #
# 현재 값의 상대 위치
# --------------------------------------------------------------------------- #


def test_position_flags_a_thin_market_right_now() -> None:
    result = compute_liquidity(_history([10] * 89 + [1]))

    assert result is not None
    assert result['position'] == POSITION_FEW


def test_position_flags_an_unusually_deep_market_right_now() -> None:
    result = compute_liquidity(_history([1] * 89 + [10]))

    assert result is not None
    assert result['position'] == POSITION_MANY


def test_position_is_normal_when_today_matches_the_usual_level() -> None:
    result = compute_liquidity(_history([5] * 90))

    assert result is not None
    assert result['position'] == POSITION_NORMAL


# --------------------------------------------------------------------------- #
# 등급
# --------------------------------------------------------------------------- #


def test_grade_liquidity_puts_availability_before_depth() -> None:
    # 매물이 아무리 두꺼워도 절반 넘게 비어 있으면 극희소다.
    assert grade_liquidity(80.0, 0.6) == GRADE_VERY_SCARCE
    # 단 하루라도 비면 '보통'이라고 부르지 않는다.
    assert grade_liquidity(80.0, 0.01) == GRADE_SCARCE
    assert grade_liquidity(19.9, 0.0) == GRADE_NORMAL
    assert grade_liquidity(20.0, 0.0) == GRADE_ABUNDANT


# --------------------------------------------------------------------------- #
# 홈 카드 압축 페이로드
# --------------------------------------------------------------------------- #


def test_build_card_scarcity_is_empty_for_healthy_supply() -> None:
    """295개 카드에 '풍부'를 붙이는 건 정보가 아니라 노이즈다."""
    assert build_card_scarcity(compute_liquidity(_history([40] * 90))) is None
    assert build_card_scarcity(compute_liquidity(_history([4] * 90))) is None
    assert build_card_scarcity(None) is None


def test_build_card_scarcity_carries_the_scarce_signal_only() -> None:
    payload = build_card_scarcity(compute_liquidity(_history([0] * 60 + [1] * 30)))

    assert payload == {'grade': GRADE_VERY_SCARCE, 'avg': 0.3, 'zero_pct': 67, 'days': 90}


# --------------------------------------------------------------------------- #
# 렌더링
# --------------------------------------------------------------------------- #


def test_liquidity_section_makes_no_claim_without_enough_samples() -> None:
    html = build_liquidity_section(None)

    assert '유동성' in html
    assert f'{MIN_LIQUIDITY_POINTS}일 미만' in html
    # 숫자를 지어내지 않는다.
    assert 'liquidity-grid' not in html
    assert 'liquidity-grade' not in html


def test_liquidity_section_renders_every_metric_and_the_grade() -> None:
    html = build_liquidity_section(compute_liquidity(_history([0] * 30 + [2] * 60)))

    assert 'liquidity-grade--scarce' in html
    assert GRADE_SCARCE in html
    assert '90일 평균 매물' in html
    assert '1.3개' in html
    assert '매물 0건인 날' in html
    assert '30일 (33%)' in html
    assert '최장 연속 0건' in html
    assert '현재 매물 위치' in html
    # 스크린리더가 등급 칩만 읽었을 때 맥락이 사라지지 않게 한다.
    assert '<span class="visually-hidden">유동성 등급 </span>' in html


def test_liquidity_section_escapes_and_reports_the_observed_range() -> None:
    html = build_liquidity_section(compute_liquidity(_history([3] * 90)))

    assert 'liquidity-grade--normal' in html
    assert '2026-01-01 ~ 2026-03-31 사이 90일을 관측한 결과입니다.' in html
    assert '현재 매물 3개' in html
    assert '기간 중앙값 3개' in html
