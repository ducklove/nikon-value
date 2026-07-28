"""rate limit 키(= 클라이언트 IP) 산출.

리버스 프록시 뒤에서는 모든 요청의 TCP 피어 주소가 프록시 IP 하나로 보이기 때문에,
`request.client.host` 를 그대로 키로 쓰면 전체 사용자가 하나의 버킷을 공유하게 된다.
(OAuth 5회/분 제한이 "서버 전체가 합쳐서 5회/분"이 되어 정상 사용자가 차단된다.)

반대로 `X-Forwarded-For` 를 무조건 신뢰하면 누구나 헤더를 위조해 요청마다 다른 키를
만들어 rate limit 을 통째로 우회할 수 있다.

그래서 **신뢰하는 프록시가 명시된 경우에만** forwarded 헤더를 읽는다:

- `TRUSTED_PROXY_IPS` 미설정  → 헤더를 무시하고 피어 주소를 그대로 사용(= 기존 동작).
- `TRUSTED_PROXY_IPS` 설정    → 피어 주소가 그 목록에 있을 때에만 헤더를 해석한다.
  헤더 해석은 uvicorn 의 `ProxyHeadersMiddleware` 와 같은 규칙을 따른다. 즉 XFF 체인을
  **오른쪽부터** 훑어 처음 만나는 "신뢰하지 않는" 주소를 클라이언트로 본다. 프록시는
  자기가 본 피어 주소를 체인 오른쪽에 덧붙이므로(nginx `$proxy_add_x_forwarded_for`),
  클라이언트가 미리 넣어 둔 위조 항목은 항상 그 왼쪽에 남아 무시된다.

자세한 배포/검증 절차는 docs/deploy-api-server.md 의 "리버스 프록시와 클라이언트 IP" 참고.
"""

from __future__ import annotations

import ipaddress
import logging

from slowapi import Limiter
from starlette.requests import Request

from server.config import TRUSTED_PROXY_IPS

logger = logging.getLogger(__name__)

# request.client 가 없는 경우(테스트용 ASGI transport, unix socket 등)의 대체 키.
# slowapi 의 get_remote_address 와 같은 값이라 기존 동작이 바뀌지 않는다.
UNKNOWN_CLIENT = "127.0.0.1"

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
_IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_ip(value: str) -> _IPAddress | None:
    """주소 문자열 하나를 IP 로 해석한다. 포트가 붙어 있으면 떼어낸다."""
    candidate = value.strip()
    if not candidate:
        return None

    # "[2001:db8::1]:443" 형태
    if candidate.startswith("["):
        end = candidate.find("]")
        if end == -1:
            return None
        candidate = candidate[1:end]
    # "203.0.113.7:443" 형태. 콜론이 하나뿐이면 IPv4:port 로 본다
    # (맨몸 IPv6 주소라면 콜론이 둘 이상이므로 여기 걸리지 않는다).
    elif candidate.count(":") == 1:
        candidate = candidate.rsplit(":", 1)[0]

    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


class ClientIPResolver:
    """`TRUSTED_PROXY_IPS` 설정 하나로부터 rate limit 키 함수를 만든다.

    설정을 생성자 인자로 받으므로 테스트에서 모듈 리로드 없이 여러 배포 형태를
    그대로 재현할 수 있다.
    """

    def __init__(self, trusted_proxies: str) -> None:
        self._hosts, self._networks = self._parse_trusted(trusted_proxies)
        self.trust_proxy_headers = bool(self._hosts or self._networks)

    @staticmethod
    def _parse_trusted(raw: str) -> tuple[frozenset[_IPAddress], tuple[_IPNetwork, ...]]:
        """설정 문자열을 주소 집합과 네트워크 목록으로 나눈다.

        해석할 수 없는 항목은 경고만 남기고 버린다. 오타("*" 포함)로 서버가 죽지는 않되,
        "신뢰하지 않음" 쪽으로 기울어 실패하도록(fail closed) 한다.
        """
        hosts: set[_IPAddress] = set()
        networks: list[_IPNetwork] = []
        for item in raw.split(","):
            entry = item.strip()
            if not entry:
                continue
            try:
                if "/" in entry:
                    networks.append(ipaddress.ip_network(entry, strict=False))
                else:
                    hosts.add(ipaddress.ip_address(entry))
            except ValueError:
                logger.warning(
                    "TRUSTED_PROXY_IPS 항목 %r 을(를) IP/CIDR 로 해석할 수 없어 무시한다. "
                    "와일드카드는 지원하지 않으며 프록시 주소를 명시해야 한다.",
                    entry,
                )
        return frozenset(hosts), tuple(networks)

    def is_trusted(self, ip: _IPAddress) -> bool:
        return ip in self._hosts or any(ip in net for net in self._networks)

    def _client_from_forwarded_for(self, header: str) -> _IPAddress | None:
        """XFF 체인에서 실제 클라이언트 주소를 고른다.

        오른쪽(= 가장 가까운 프록시가 덧붙인 쪽)부터 훑어 처음 만나는 비신뢰 주소를 쓴다.
        해석할 수 없는 항목을 만나면 체인을 믿을 수 없다고 보고 즉시 포기한다.
        모든 항목이 신뢰 프록시면(= 내부 경유) None 을 돌려주고 피어 주소로 되돌아간다.
        """
        for raw in reversed(header.split(",")):
            if not raw.strip():
                continue
            ip = parse_ip(raw)
            if ip is None:
                return None
            if not self.is_trusted(ip):
                return ip
        return None

    def __call__(self, request: Request) -> str:
        """rate limit 버킷을 가르는 키를 돌려준다.

        신뢰 프록시가 설정되지 않았거나 피어가 그 목록에 없으면 피어 주소를 그대로 쓴다.
        따라서 프록시가 없는 배포에서는 위조 헤더가 아무 영향도 주지 못한다.
        """
        peer = request.client.host if request.client and request.client.host else UNKNOWN_CLIENT
        if not self.trust_proxy_headers:
            return peer

        parsed_peer = parse_ip(peer)
        if parsed_peer is None or not self.is_trusted(parsed_peer):
            # 신뢰 프록시를 거치지 않고 직접 들어온 요청. 헤더를 믿지 않는다.
            return peer

        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client = self._client_from_forwarded_for(forwarded)
            if client is not None:
                return str(client)

        # XFF 가 없을 때만 X-Real-IP 를 본다. 신뢰 프록시가 직접 넣은 단일 값이므로
        # 체인 해석 없이 그대로 쓴다(nginx 의 `proxy_set_header X-Real-IP $remote_addr`).
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            client = parse_ip(real_ip)
            if client is not None:
                return str(client)

        return peer


_resolver = ClientIPResolver(TRUSTED_PROXY_IPS)
TRUST_PROXY_HEADERS = _resolver.trust_proxy_headers


def client_ip(request: Request) -> str:
    """slowapi 의 key_func. 인자 이름은 반드시 `request` 여야 한다.

    (slowapi 는 `inspect.signature(key_func)` 에 "request" 파라미터가 있는지 보고
    호출 방식을 정한다 — extension.py 의 `_inject_headers` 인근 참고.)
    """
    return _resolver(request)


limiter = Limiter(key_func=client_ip)

if TRUST_PROXY_HEADERS:
    logger.info(
        "rate limit: 신뢰 프록시를 거친 요청에 한해 X-Forwarded-For/X-Real-IP 를 "
        "클라이언트 IP 로 쓴다 (TRUSTED_PROXY_IPS=%s).",
        TRUSTED_PROXY_IPS,
    )
else:
    logger.info(
        "rate limit: TRUSTED_PROXY_IPS 미설정 — forwarded 헤더를 무시하고 TCP 피어 주소를 키로 쓴다."
    )
