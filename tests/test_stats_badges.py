from __future__ import annotations

from nikon_value.sitegen.data import is_at_yearly_low


def _history(medians):
    return [{"date": f"2026-01-{i + 1:02d}", "median": m} for i, m in enumerate(medians)]


def test_requires_minimum_sample_size():
    # 29개 표본까지는 최저여도 주장하지 않는다
    assert not is_at_yearly_low(_history([100.0] * 28 + [50.0]))
    assert is_at_yearly_low(_history([100.0] * 29 + [50.0]))


def test_latest_must_be_the_minimum():
    base = [100.0 + i for i in range(30)]
    assert not is_at_yearly_low(_history(base))            # 최신값이 최고
    assert is_at_yearly_low(_history(base[::-1]))          # 최신값이 최저
    assert is_at_yearly_low(_history([100.0] * 30))        # 동률 최저(<=)도 인정


def test_none_medians_are_ignored():
    medians = [100.0] * 30 + [None, 90.0]
    history = _history([m for m in medians])
    assert is_at_yearly_low(history)
    # None만 가득하면 표본 부족
    assert not is_at_yearly_low(_history([None] * 40))
