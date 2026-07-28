"""매물 수(count) 시계열에서 유동성 지표를 계산한다.

왜 이 지표인가
------------------------------------------------------------------------------
희귀 모델을 찾는 구매자에게는 "얼마인가"만큼 "구할 수 있는가"가 중요하다.
count 시계열은 이미 수집·저장되고 있었지만 사이트 어디에도 노출되지 않았다.

실제 데이터(331개 제품 × 최근 90일)를 보고 고른 지표는 네 가지다.

1. 평균 매물 수 (avg_count)
   "평소 몇 개나 나와 있나". 전 제품 분포는 중앙값 26개, 하위 10%가 1개 이하로
   꼬리가 매우 길다. 절대 수준을 그대로 보여 주는 게 가장 직관적이다.

2. 매물 0건인 날 비율 (zero_ratio)
   331개 중 36개만 0건인 날을 가진다. 즉 이 지표는 대부분의 제품에서 0%이고
   소수의 희소 모델에서만 신호가 된다 — 노이즈가 적고 변별력이 크다.

3. 최장 연속 0건 (max_zero_streak)
   같은 zero_ratio라도 "띄엄띄엄 하루씩 비었다"와 "85일 연속 매물이 없었다"는
   구매자에게 전혀 다른 의미다. 실제로 zero_ratio가 0과 1 사이인 13개 제품의
   최장 공백은 1일부터 85일까지 퍼져 있었다. 대기 시간의 현실적인 하한선이다.

4. 현재 매물 수의 상대 위치 (position)
   "지금이 물량이 많은 시기인가". 평균 3개 이상인 제품의 (최대-최소)/평균은
   중앙값 0.54로, 지금 시점이 평소보다 두꺼운지 얇은지는 실제로 갈린다.

표본 부족 처리
------------------------------------------------------------------------------
이 저장소에는 이미 같은 규율이 있다: is_at_yearly_low()는 표본 30일 미만이면
"1년 최저"를 주장하지 않는다(sitegen/data.py). 유동성도 같은 기준을 따른다 —
창 안의 관측일이 MIN_LIQUIDITY_POINTS 미만이면 아무 주장도 하지 않고 None을
돌려준다. 호출부는 그때 "관측일이 부족합니다"라고만 표시한다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# 유동성 창: 최근 90일(경계 포함). 계절성보다 "지금 구할 수 있나"에 답하는 길이다.
LIQUIDITY_WINDOW_DAYS = 90

# 이 미만이면 유동성을 주장하지 않는다. is_at_yearly_low()와 같은 30일 기준.
MIN_LIQUIDITY_POINTS = 30

# 등급 임계값. zero_ratio가 먼저다 — 구매자에게는 "평소 몇 개"보다
# "아예 없는 날이 있나"가 결정적이기 때문이다.
SCARCE_ZERO_RATIO = 0.5  # 이 이상이면 극희소
ABUNDANT_AVG_COUNT = 20.0  # 이 이상이면서 0건인 날이 없으면 풍부

GRADE_VERY_SCARCE = '극희소'
GRADE_SCARCE = '희소'
GRADE_NORMAL = '보통'
GRADE_ABUNDANT = '풍부'

# 홈 카드에 압축 노출할 등급. 295개 카드에 "풍부"를 붙이는 건 정보가 아니라
# 노이즈다. 구매 판단을 바꾸는 희소 신호에서만 칩을 띄운다.
CARD_VISIBLE_GRADES = (GRADE_VERY_SCARCE, GRADE_SCARCE)

# 현재 매물 수의 상대 위치 라벨 경계(백분위).
POSITION_HIGH_PCT = 70.0
POSITION_LOW_PCT = 30.0

POSITION_MANY = 'many'
POSITION_NORMAL = 'normal'
POSITION_FEW = 'few'

POSITION_LABELS = {
    POSITION_MANY: '많은 편',
    POSITION_NORMAL: '평균 수준',
    POSITION_FEW: '적은 편',
}


def _usable_entries(history: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """(날짜, 매물 수) 쌍을 날짜순으로 정규화한다.

    실제 데이터에는 같은 날짜가 두 번 들어간 제품이 20개 있다(수집 재실행).
    같은 날짜는 마지막 값만 남겨야 평균·0건 비율이 특정 날에 두 번 반영되지
    않는다. 날짜가 없거나 dict가 아닌 항목은 버린다. count는 None/누락을
    0으로 본다 — 수집은 됐지만 매물이 없었던 날과 같은 의미이기 때문이다.
    """
    by_date: dict[str, int] = {}
    for entry in history or []:
        if not isinstance(entry, dict):
            continue
        entry_date = entry.get('date')
        if not isinstance(entry_date, str) or not entry_date:
            continue
        raw_count = entry.get('count')
        try:
            count = int(raw_count or 0)
        except (TypeError, ValueError):
            count = 0
        by_date[entry_date] = max(count, 0)
    return sorted(by_date.items())


def _max_zero_streak(counts: list[int]) -> int:
    longest = 0
    current = 0
    for count in counts:
        current = current + 1 if count == 0 else 0
        longest = max(longest, current)
    return longest


def _percentile_rank(counts: list[int], value: int) -> float:
    """중간 순위(mid-rank) 백분위. 동점은 절반만 인정해 0/0 같은 자리에서
    과대 평가되지 않게 한다."""
    below = sum(1 for count in counts if count < value)
    equal = sum(1 for count in counts if count == value)
    return ((below + equal / 2) / len(counts)) * 100


def _position(counts: list[int], current: int) -> str | None:
    # 창 전체가 0건이면 "위치"라고 부를 분포 자체가 없다.
    if max(counts) == 0:
        return None
    rank = _percentile_rank(counts, current)
    if rank >= POSITION_HIGH_PCT:
        return POSITION_MANY
    if rank <= POSITION_LOW_PCT:
        return POSITION_FEW
    return POSITION_NORMAL


def _median(values: list[int]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def grade_liquidity(avg_count: float, zero_ratio: float) -> str:
    """평균 매물 수와 0건 비율로 등급을 매긴다."""
    if zero_ratio >= SCARCE_ZERO_RATIO:
        return GRADE_VERY_SCARCE
    if zero_ratio > 0:
        return GRADE_SCARCE
    if avg_count >= ABUNDANT_AVG_COUNT:
        return GRADE_ABUNDANT
    return GRADE_NORMAL


def compute_liquidity(
    history: list[dict[str, Any]],
    *,
    window_days: int = LIQUIDITY_WINDOW_DAYS,
    min_points: int = MIN_LIQUIDITY_POINTS,
) -> dict[str, Any] | None:
    """최근 window_days일 매물 수에서 유동성 지표를 계산한다.

    창의 기준일은 '오늘'이 아니라 마지막 관측일이다. 수집이 며칠 멈춰도
    지표가 조용히 0으로 희석되지 않게 하기 위해서다(compute_price_change와
    같은 관례).

    관측일이 min_points 미만이면 None — 주장을 하지 않는다.
    """
    entries = _usable_entries(history)
    if not entries:
        return None

    try:
        latest_date = date.fromisoformat(entries[-1][0])
    except ValueError:
        return None

    cutoff = (latest_date - timedelta(days=max(window_days, 1) - 1)).isoformat()
    window = [(entry_date, count) for entry_date, count in entries if entry_date >= cutoff]
    if len(window) < min_points:
        return None

    counts = [count for _, count in window]
    days = len(counts)
    zero_days = sum(1 for count in counts if count == 0)
    zero_ratio = zero_days / days
    avg_count = sum(counts) / days
    current = counts[-1]

    return {
        'window_days': window_days,
        'days': days,
        'first_date': window[0][0],
        'last_date': window[-1][0],
        'avg_count': round(avg_count, 1),
        'median_count': _median(counts),
        'min_count': min(counts),
        'max_count': max(counts),
        'zero_days': zero_days,
        'zero_ratio': round(zero_ratio, 4),
        'max_zero_streak': _max_zero_streak(counts),
        'current': current,
        'position': _position(counts, current),
        'grade': grade_liquidity(avg_count, zero_ratio),
    }


def build_card_scarcity(liquidity: dict[str, Any] | None) -> dict[str, Any] | None:
    """홈 카드용 압축 페이로드. 희소 신호가 없으면 None(=카드에 아무것도 안 붙음)."""
    if not liquidity or liquidity['grade'] not in CARD_VISIBLE_GRADES:
        return None
    return {
        'grade': liquidity['grade'],
        'avg': liquidity['avg_count'],
        'zero_pct': round(liquidity['zero_ratio'] * 100),
        'days': liquidity['days'],
    }
