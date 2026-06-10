"""USD/KRW 환율 조회 (ECB 기준환율) 및 복구 폴백."""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET

import requests

from nikon_value.paths import DATA_DIR

log = logging.getLogger(__name__)

ECB_EXCHANGE_RATES_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


def _recover_exchange_rate_from_daily() -> dict | None:
    """가장 최근 일별 스냅샷에서 환율 정보를 복구합니다."""
    daily_dir = DATA_DIR / "daily"
    if not daily_dir.exists():
        return None
    for f in sorted(daily_dir.glob("*.json"), reverse=True)[:5]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            rate = data.get("exchange_rate")
            if rate and rate.get("rate"):
                return rate
        except Exception:
            continue
    return None


def fetch_usd_krw_exchange_rate() -> dict[str, object]:
    """ECB 일일 기준환율에서 USD/KRW 환산값을 가져옵니다."""
    resp = requests.get(ECB_EXCHANGE_RATES_URL, timeout=30)
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
