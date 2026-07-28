"""rate limit 키 산출 검증.

핵심은 두 가지다.

1. 리버스 프록시 뒤에서는 사용자별로 키가 갈라져야 한다(공유 버킷 방지).
2. 신뢰하지 않는 경로로 들어온 `X-Forwarded-For` 는 절대 키를 바꾸지 못해야 한다
   (위조로 rate limit 을 우회하는 시나리오 방지).
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from server.rate_limit import UNKNOWN_CLIENT, ClientIPResolver, client_ip, parse_ip


def make_request(peer: str | None, **headers: str) -> Request:
    """주어진 TCP 피어 주소와 헤더를 가진 Request 를 만든다."""
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/auth/google",
        "raw_path": b"/auth/google",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (key.replace("_", "-").lower().encode(), value.encode())
            for key, value in headers.items()
        ],
        "client": (peer, 51234) if peer is not None else None,
        "server": ("testserver", 80),
    }
    return Request(scope)


NO_PROXY = ClientIPResolver("")
LOCAL_PROXY = ClientIPResolver("127.0.0.1")
LAN_PROXY = ClientIPResolver("192.168.0.0/24")


# --------------------------------------------------------------------------
# TRUSTED_PROXY_IPS 미설정: 기존 동작(피어 주소)을 그대로 유지해야 한다
# --------------------------------------------------------------------------


def test_no_trusted_proxy_uses_peer_address():
    assert NO_PROXY.trust_proxy_headers is False
    assert NO_PROXY(make_request("203.0.113.7")) == "203.0.113.7"


def test_no_trusted_proxy_ignores_forged_forwarded_for():
    """위조 시나리오: 프록시가 없는 배포에 XFF 를 끼워 넣어도 키가 바뀌면 안 된다."""
    honest = NO_PROXY(make_request("203.0.113.7"))
    forged = NO_PROXY(make_request("203.0.113.7", x_forwarded_for="1.2.3.4"))
    assert forged == honest == "203.0.113.7"


def test_no_trusted_proxy_ignores_forged_real_ip():
    forged = NO_PROXY(make_request("203.0.113.7", x_real_ip="1.2.3.4"))
    assert forged == "203.0.113.7"


def test_forged_headers_cannot_split_buckets_without_proxy():
    """공격자가 매 요청 다른 XFF 를 보내도 키가 하나로 유지되어야 rate limit 이 산다."""
    keys = {
        NO_PROXY(make_request("203.0.113.7", x_forwarded_for=f"10.0.0.{i}"))
        for i in range(1, 30)
    }
    assert keys == {"203.0.113.7"}


# --------------------------------------------------------------------------
# TRUSTED_PROXY_IPS 설정: 신뢰 프록시를 거친 요청만 헤더를 인정한다
# --------------------------------------------------------------------------


def test_trusted_proxy_uses_forwarded_for():
    assert LOCAL_PROXY.trust_proxy_headers is True
    assert LOCAL_PROXY(make_request("127.0.0.1", x_forwarded_for="203.0.113.7")) == "203.0.113.7"


def test_trusted_proxy_splits_buckets_per_user():
    """프록시 뒤에서도 사용자별로 키가 갈라져야 공유 버킷 문제가 사라진다."""
    first = LOCAL_PROXY(make_request("127.0.0.1", x_forwarded_for="203.0.113.7"))
    second = LOCAL_PROXY(make_request("127.0.0.1", x_forwarded_for="198.51.100.9"))
    assert first != second


def test_forged_prefix_entries_are_ignored():
    """위조 시나리오: 클라이언트가 XFF 를 미리 넣어도 프록시가 덧붙인 오른쪽 값이 이긴다.

    nginx 의 `$proxy_add_x_forwarded_for` 는 자기가 본 피어 주소를 체인 오른쪽에
    덧붙인다. 따라서 클라이언트가 심어 둔 값은 항상 왼쪽에 남는다.
    """
    key = LOCAL_PROXY(
        make_request("127.0.0.1", x_forwarded_for="1.2.3.4, 5.6.7.8, 203.0.113.7")
    )
    assert key == "203.0.113.7"


def test_forged_entries_cannot_split_buckets_behind_proxy():
    """같은 공격자가 앞쪽 항목만 바꿔 가며 보내도 키는 실제 주소 하나로 고정된다."""
    keys = {
        LOCAL_PROXY(make_request("127.0.0.1", x_forwarded_for=f"10.0.0.{i}, 203.0.113.7"))
        for i in range(1, 30)
    }
    assert keys == {"203.0.113.7"}


def test_untrusted_peer_forwarded_for_is_ignored():
    """위조 시나리오: 프록시를 우회해 서버 포트로 직접 붙은 요청은 헤더를 못 쓴다."""
    key = LOCAL_PROXY(make_request("203.0.113.7", x_forwarded_for="1.2.3.4"))
    assert key == "203.0.113.7"


def test_untrusted_peer_real_ip_is_ignored():
    key = LOCAL_PROXY(make_request("203.0.113.7", x_real_ip="1.2.3.4"))
    assert key == "203.0.113.7"


def test_proxy_chain_picks_first_untrusted_from_right():
    """프록시 두 단(예: 엣지 → 내부)일 때 체인 오른쪽의 신뢰 항목은 건너뛴다."""
    resolver = ClientIPResolver("127.0.0.1,192.168.0.10")
    key = resolver(
        make_request("127.0.0.1", x_forwarded_for="1.2.3.4, 203.0.113.7, 192.168.0.10")
    )
    assert key == "203.0.113.7"


def test_all_entries_trusted_falls_back_to_peer():
    """체인 전체가 내부 프록시면(= 내부 호출) 피어 주소로 되돌아간다."""
    key = LOCAL_PROXY(make_request("127.0.0.1", x_forwarded_for="127.0.0.1"))
    assert key == "127.0.0.1"


def test_unparseable_forwarded_entry_falls_back_to_peer():
    """오른쪽 끝이 IP 가 아니면 체인을 믿지 않고 피어 주소를 쓴다(fail closed)."""
    key = LOCAL_PROXY(make_request("127.0.0.1", x_forwarded_for="203.0.113.7, unknown"))
    assert key == "127.0.0.1"


def test_empty_forwarded_header_falls_back_to_peer():
    key = LOCAL_PROXY(make_request("127.0.0.1", x_forwarded_for=""))
    assert key == "127.0.0.1"


def test_real_ip_used_only_when_forwarded_for_absent():
    assert LOCAL_PROXY(make_request("127.0.0.1", x_real_ip="203.0.113.7")) == "203.0.113.7"
    # XFF 가 있으면 그쪽이 우선한다.
    key = LOCAL_PROXY(
        make_request("127.0.0.1", x_forwarded_for="198.51.100.9", x_real_ip="203.0.113.7")
    )
    assert key == "198.51.100.9"


def test_cidr_trust_matches_whole_range():
    assert LAN_PROXY(make_request("192.168.0.10", x_forwarded_for="203.0.113.7")) == "203.0.113.7"
    # 대역 밖의 피어는 신뢰하지 않는다.
    assert LAN_PROXY(make_request("192.168.1.10", x_forwarded_for="203.0.113.7")) == "192.168.1.10"


# --------------------------------------------------------------------------
# 설정 오류 / 형식 처리
# --------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["*", "all", "not-an-ip", "0.0.0.0/99", " , "])
def test_invalid_config_does_not_enable_trust(raw):
    """위조 시나리오: '*' 같은 값으로 전부 신뢰시키려는 설정은 무시된다(fail closed)."""
    resolver = ClientIPResolver(raw)
    assert resolver.trust_proxy_headers is False
    assert resolver(make_request("127.0.0.1", x_forwarded_for="1.2.3.4")) == "127.0.0.1"


def test_partially_invalid_config_keeps_valid_entries():
    resolver = ClientIPResolver("*, 127.0.0.1")
    assert resolver.trust_proxy_headers is True
    assert resolver(make_request("127.0.0.1", x_forwarded_for="203.0.113.7")) == "203.0.113.7"
    # "*" 는 버려졌으므로 임의 피어는 여전히 신뢰되지 않는다.
    assert resolver(make_request("198.51.100.1", x_forwarded_for="203.0.113.7")) == "198.51.100.1"


def test_missing_client_falls_back_to_loopback():
    """ASGI transport 처럼 client 정보가 없는 경우 기존 slowapi 동작과 같은 값을 쓴다."""
    assert NO_PROXY(make_request(None)) == UNKNOWN_CLIENT


def test_forwarded_entry_port_is_stripped():
    assert LOCAL_PROXY(make_request("127.0.0.1", x_forwarded_for="203.0.113.7:443")) == "203.0.113.7"


def test_ipv6_forwarded_entry_is_canonicalized():
    """표기가 달라도 같은 주소면 같은 버킷이어야 한다."""
    resolver = ClientIPResolver("::1")
    expanded = resolver(make_request("::1", x_forwarded_for="2001:0db8:0000:0000:0000:0000:0000:0001"))
    compact = resolver(make_request("::1", x_forwarded_for="2001:db8::1"))
    bracketed = resolver(make_request("::1", x_forwarded_for="[2001:db8::1]:443"))
    assert expanded == compact == bracketed == "2001:db8::1"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("203.0.113.7", "203.0.113.7"),
        (" 203.0.113.7 ", "203.0.113.7"),
        ("203.0.113.7:8080", "203.0.113.7"),
        ("[2001:db8::1]", "2001:db8::1"),
        ("2001:db8::1", "2001:db8::1"),
    ],
)
def test_parse_ip_accepts_common_forms(raw, expected):
    parsed = parse_ip(raw)
    assert parsed is not None
    assert str(parsed) == expected


@pytest.mark.parametrize("raw", ["", "  ", "unknown", "[2001:db8::1", "999.1.1.1", "_hidden"])
def test_parse_ip_rejects_junk(raw):
    assert parse_ip(raw) is None


# --------------------------------------------------------------------------
# 기본 설정(= 환경변수 미설정)에서의 모듈 수준 동작
# --------------------------------------------------------------------------


def test_module_default_ignores_forwarded_headers():
    """테스트 환경에는 TRUSTED_PROXY_IPS 가 없으므로 헤더가 무시되어야 한다."""
    assert client_ip(make_request("203.0.113.7", x_forwarded_for="1.2.3.4")) == "203.0.113.7"


@pytest.mark.asyncio
async def test_client_ip_probe_endpoint(client):
    """운영자 진단 엔드포인트가 위조 헤더에 흔들리지 않는지 함께 확인한다."""
    resp = await client.get("/health/client-ip", headers={"X-Forwarded-For": "1.2.3.4"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["trust_proxy_headers"] is False
    assert data["x_forwarded_for"] == "1.2.3.4"
    # 헤더를 신뢰하지 않으므로 rate limit 키는 위조 값이 아니어야 한다.
    assert data["client_ip"] != "1.2.3.4"
