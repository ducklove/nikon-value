from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Channel(Protocol):
    """알림 채널 인터페이스.

    구현은 예외를 밖으로 내보내지 않고 성공 여부만 반환해야 한다.
    호출 측이 '발송 성공 시에만 상태 소비' 패턴으로 재시도를 다루기 때문이다.
    """

    async def send(self, subject: str, body: str) -> bool: ...
