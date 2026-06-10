"""알림 채널 어댑터 모음.

채널별 HTTP 호출만 담당하는 공용 패키지. 사용자-채널 매핑, 토큰 저장/갱신,
재시도 정책은 사용하는 앱이 소유한다. README 참고.
"""

from notifykit.base import Channel
from notifykit.kakao import KakaoMemoChannel
from notifykit.telegram import TelegramChannel

__version__ = "0.1.0"

__all__ = ["Channel", "KakaoMemoChannel", "TelegramChannel", "__version__"]
