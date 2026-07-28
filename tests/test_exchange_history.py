"""환율 시계열(data/exchange-rates.json) 저장·복구 폴백과 ECB 조회 테스트.

ECB 조회는 세션을 주입해 스텁으로 대체한다 — 실제 ECB는 절대 호출하지 않는다.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from nikon_value.exchange import (
    ECB_EXCHANGE_RATES_URL,
    append_exchange_rate,
    fetch_usd_krw_exchange_rate,
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


def test_load_history_ignores_a_payload_whose_rates_are_not_a_list(tmp_path):
    path = tmp_path / "exchange-rates.json"
    path.write_text(json.dumps({"updated": "2026-07-28", "rates": {}}), encoding="utf-8")

    assert load_exchange_rate_history(path) == []


# --------------------------------------------------------------------------- #
# fetch_usd_krw_exchange_rate — ECB XML 파싱 (네트워크 호출 없음)
# --------------------------------------------------------------------------- #

ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
 <gesmes:subject>Reference rates</gesmes:subject>
 <Cube>
  <Cube time="2026-07-28">
   <Cube currency="USD" rate="1.1723"/>
   <Cube currency="KRW" rate="1621.35"/>
   <Cube currency="JPY"/>
  </Cube>
 </Cube>
</gesmes:Envelope>
"""


class _StubResponse:
    def __init__(self, body: str):
        self.content = body.encode("utf-8")
        self.raised = False

    def raise_for_status(self):
        self.raised = True


class _StubSession:
    """requests.Session의 get만 흉내내는 스텁."""

    def __init__(self, body: str):
        self.response = _StubResponse(body)
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append((url, timeout))
        return self.response


def test_fetch_converts_ecb_eur_rates_into_a_usd_krw_rate():
    session = _StubSession(ECB_XML)

    result = fetch_usd_krw_exchange_rate(session=session)

    assert result == {
        "base": "USD",
        "quote": "KRW",
        "rate": round(1621.35 / 1.1723, 4),
        "reference_date": "2026-07-28",
        "source": "ECB reference rates",
    }
    assert session.calls == [(ECB_EXCHANGE_RATES_URL, 30)]
    assert session.response.raised, "HTTP 오류 응답을 그대로 파싱하면 안 된다"


def test_fetch_rejects_a_payload_without_a_dated_cube():
    body = ECB_XML.replace('<Cube time="2026-07-28">', "<Cube>")

    with pytest.raises(ValueError, match="dated Cube"):
        fetch_usd_krw_exchange_rate(session=_StubSession(body))


@pytest.mark.parametrize("currency", ["USD", "KRW"])
def test_fetch_rejects_a_payload_missing_usd_or_krw(currency):
    body = ECB_XML.replace(f'<Cube currency="{currency}"', '<Cube currency="CHF"')

    with pytest.raises(ValueError, match="USD and KRW"):
        fetch_usd_krw_exchange_rate(session=_StubSession(body))


def test_fetch_propagates_a_malformed_xml_payload():
    with pytest.raises(ET.ParseError):
        fetch_usd_krw_exchange_rate(session=_StubSession("<Envelope><Cube"))


def test_fetch_honours_a_custom_url():
    session = _StubSession(ECB_XML)

    fetch_usd_krw_exchange_rate(session=session, url="https://mirror.test/eurofxref-daily.xml")

    assert session.calls == [("https://mirror.test/eurofxref-daily.xml", 30)]


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
