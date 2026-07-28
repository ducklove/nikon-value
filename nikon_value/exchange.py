"""USD/KRW 환율 조회 (ECB 기준환율), 환율 시계열 저장 및 복구 폴백."""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from nikon_value.paths import EXCHANGE_RATES_PATH

log = logging.getLogger(__name__)

ECB_EXCHANGE_RATES_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


def load_exchange_rate_history(path: Path = EXCHANGE_RATES_PATH) -> list[dict]:
    """환율 시계열을 날짜 오름차순으로 로드합니다."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Exchange rate history unreadable (%s)", exc)
        return []
    rates = data.get("rates") if isinstance(data, dict) else data
    if not isinstance(rates, list):
        return []
    return [r for r in rates if isinstance(r, dict) and r.get("date")]


def recover_exchange_rate_from_history(path: Path = EXCHANGE_RATES_PATH) -> dict | None:
    """환율 시계열의 가장 최근 기록에서 환율 정보를 복구합니다."""
    for record in reversed(load_exchange_rate_history(path)):
        if record.get("rate"):
            return {k: v for k, v in record.items() if k != "date"}
    return None


def append_exchange_rate(
    exchange_rate: dict | None,
    date_str: str,
    path: Path = EXCHANGE_RATES_PATH,
) -> bool:
    """환율 시계열에 오늘 값을 append합니다. 같은 날짜는 교체."""
    if not exchange_rate or not exchange_rate.get("rate"):
        return False

    history = [r for r in load_exchange_rate_history(path) if r.get("date") != date_str]
    history.append({"date": date_str, **exchange_rate})
    history.sort(key=lambda r: r["date"])

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"updated": date_str, "rates": history}, f, ensure_ascii=False, indent=1)
    return True


def fetch_usd_krw_exchange_rate(
    *,
    session: requests.Session | None = None,
    url: str = ECB_EXCHANGE_RATES_URL,
) -> dict[str, object]:
    """ECB 일일 기준환율에서 USD/KRW 환산값을 가져옵니다.

    session을 주면 그 세션으로 호출한다(테스트 스텁 주입 지점).
    기본값은 지금까지와 같은 모듈 수준 requests.get이다.
    """
    http_get = requests.get if session is None else session.get
    resp = http_get(url, timeout=30)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    cube_with_time = root.find(".//{*}Cube[@time]")
    if cube_with_time is None:
        raise ValueError("ECB exchange rate payload does not include a dated Cube node")

    rates: dict[str, float] = {}
    for entry in cube_with_time.findall("{*}Cube"):
        currency = entry.attrib.get("currency")
        rate = entry.attrib.get("rate")
        if currency and rate:
            rates[currency] = float(rate)

    usd_per_eur = rates.get("USD")
    krw_per_eur = rates.get("KRW")
    if not usd_per_eur or not krw_per_eur:
        raise ValueError("ECB daily rates do not include both USD and KRW")

    usd_to_krw = krw_per_eur / usd_per_eur
    return {
        "base": "USD",
        "quote": "KRW",
        "rate": round(usd_to_krw, 4),
        "reference_date": cube_with_time.attrib["time"],
        "source": "ECB reference rates",
    }
