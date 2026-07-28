"""환율 시계열(data/exchange-rates.json) 저장·복구 폴백 테스트."""

from __future__ import annotations

import json

from nikon_value.exchange import (
    append_exchange_rate,
    load_exchange_rate_history,
    recover_exchange_rate_from_history,
)
from nikon_value.paths import EXCHANGE_RATES_PATH

RATE = {
    "base": "USD",
    "quote": "KRW",
    "rate": 1459.4528,
    "reference_date": "2026-07-28",
    "source": "ECB reference rates",
}


def test_load_history_returns_empty_for_a_missing_file(tmp_path):
    assert load_exchange_rate_history(tmp_path / "nope.json") == []


def test_load_history_tolerates_a_corrupt_file(tmp_path):
    path = tmp_path / "exchange-rates.json"
    path.write_text("{broken", encoding="utf-8")

    assert load_exchange_rate_history(path) == []


def test_append_creates_the_file_and_keeps_dates_ascending(tmp_path):
    path = tmp_path / "exchange-rates.json"

    assert append_exchange_rate(RATE, "2026-07-28", path=path) is True
    assert append_exchange_rate({**RATE, "rate": 1400.0}, "2026-07-01", path=path) is True

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [r["date"] for r in payload["rates"]] == ["2026-07-01", "2026-07-28"]
    assert payload["updated"] == "2026-07-01"  # 마지막으로 기록한 날짜


def test_append_replaces_an_existing_date_instead_of_duplicating(tmp_path):
    path = tmp_path / "exchange-rates.json"
    append_exchange_rate(RATE, "2026-07-28", path=path)
    append_exchange_rate({**RATE, "rate": 1470.0}, "2026-07-28", path=path)

    history = load_exchange_rate_history(path)
    assert len(history) == 1
    assert history[0]["rate"] == 1470.0


def test_append_ignores_a_missing_or_rateless_payload(tmp_path):
    path = tmp_path / "exchange-rates.json"

    assert append_exchange_rate(None, "2026-07-28", path=path) is False
    assert append_exchange_rate({"base": "USD"}, "2026-07-28", path=path) is False
    assert not path.exists()


def test_recover_returns_the_latest_rate_without_the_date_key(tmp_path):
    path = tmp_path / "exchange-rates.json"
    append_exchange_rate({**RATE, "rate": 1400.0}, "2026-07-01", path=path)
    append_exchange_rate(RATE, "2026-07-28", path=path)

    recovered = recover_exchange_rate_from_history(path)

    assert recovered == RATE
    assert "date" not in recovered


def test_recover_returns_none_when_there_is_nothing_to_recover(tmp_path):
    assert recover_exchange_rate_from_history(tmp_path / "nope.json") is None

    path = tmp_path / "exchange-rates.json"
    path.write_text(json.dumps({"rates": [{"date": "2026-07-28"}]}), encoding="utf-8")
    assert recover_exchange_rate_from_history(path) is None


def test_shipped_exchange_rate_history_is_sorted_and_unique():
    """이관된 data/exchange-rates.json이 오름차순·중복 없음인지 확인한다."""
    history = load_exchange_rate_history(EXCHANGE_RATES_PATH)
    if not history:
        return

    dates = [r["date"] for r in history]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))
    assert all(r.get("rate") for r in history)
    assert recover_exchange_rate_from_history(EXCHANGE_RATES_PATH)["rate"] == history[-1]["rate"]
