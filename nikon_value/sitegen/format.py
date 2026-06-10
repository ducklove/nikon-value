"""금액·환율·변동률 표시 등 순수 포매팅 헬퍼."""

from __future__ import annotations

import json
from html import escape
from typing import Any


def format_money(value: Any) -> str:
    if value is None:
        return '-'
    amount = float(value)
    if amount.is_integer():
        return f'${int(amount):,}'
    return f'${amount:,.2f}'


def render_money_span(value: Any, *, extra_class: str = '', sign: str = 'auto') -> str:
    classes = 'money-value'
    if extra_class:
        classes = f'{classes} {extra_class}'
    if value is None:
        return f'<span class="{classes}">{escape(format_money(value))}</span>'
    amount = float(value)
    return (
        f'<span class="{classes}" data-money-usd="{escape(str(amount))}"'
        f' data-money-sign="{escape(sign)}">{escape(format_money(amount))}</span>'
    )


def render_money_range(start: Any, end: Any, *, extra_class: str = '') -> str:
    classes = 'money-range'
    if extra_class:
        classes = f'{classes} {extra_class}'
    if start is None or end is None:
        return f'<span class="{classes}">-</span>'
    return (
        f'<span class="{classes}">'
        f'{render_money_span(start, extra_class=extra_class)}'
        ' - '
        f'{render_money_span(end, extra_class=extra_class)}'
        '</span>'
    )


def format_exchange_rate(exchange_rate: dict[str, Any] | None) -> str:
    if not exchange_rate or exchange_rate.get('rate') is None:
        return 'KRW 환산용 환율 데이터를 불러오지 못했습니다.'
    rate = float(exchange_rate['rate'])
    reference_date = exchange_rate.get('reference_date') or '-'
    source = exchange_rate.get('source') or '환율 데이터'
    return f'USD 1 = KRW {rate:,.2f} ({source} {reference_date} 기준)'


def format_exchange_rate_inline(exchange_rate: dict[str, Any] | None) -> str:
    if not exchange_rate or exchange_rate.get('rate') is None:
        return ''
    return f' (USD/KRW = {float(exchange_rate["rate"]):,.2f})'


def json_script(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False).replace('</script>', '<\\/script>')


def format_change_percent(change: dict[str, Any] | None) -> str:
    if not change:
        return '-'
    value = change['delta_pct']
    sign = '+' if value > 0 else ''
    return f'{sign}{value:.1f}%'


def format_change_value(change: dict[str, Any] | None) -> str:
    if not change:
        return '-'
    value = change['delta_value']
    sign = '+' if value > 0 else ''
    return f'{sign}{format_money(value)}'
