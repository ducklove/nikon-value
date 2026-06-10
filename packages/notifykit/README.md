# notifykit

텔레그램·카카오톡 알림 발송 채널 어댑터 모음. 여러 프로젝트에서 공용으로 쓰기 위한
패키지로, 채널별 HTTP 호출만 담당한다.

## 설계 원칙

- **알림만 담당한다.** 인증(OAuth)·사용자 관리·DB는 사용하는 앱의 소관이다.
  - 사용자 → 채널 매핑 저장, 토큰 저장/갱신, 재시도·중복 방지 정책은 앱이 소유한다.
  - 카카오톡처럼 사용자 토큰이 필요한 채널은 `token_provider` 콜백으로 토큰을 공급받는다.
- **`send()`는 예외를 던지지 않는다.** 성공 여부를 `bool`로만 반환하므로,
  "발송 성공 시에만 상태를 소비"하는 재시도 패턴과 자연스럽게 맞물린다.
- 의존성은 `httpx` 하나다. 웹 프레임워크·DB에 결합하지 않는다.

## 설치

```bash
# 이 저장소(모노레포) 안에서
pip install ./packages/notifykit

# 다른 프로젝트에서 (버전 태그로 고정 권장)
pip install "git+https://github.com/ducklove/nikon-value.git@master#subdirectory=packages/notifykit"
```

## 사용

### 텔레그램

[@BotFather](https://t.me/BotFather)로 봇을 만들고, 받는 사람이 봇에게 먼저 말을 건 뒤
chat_id([@userinfobot](https://t.me/userinfobot) 또는 `getUpdates`로 확인)를 등록한다.

```python
from notifykit import TelegramChannel

channel = TelegramChannel(bot_token="123:abc", chat_id="987654321")
ok = await channel.send("제목", "본문")  # bool
```

### 카카오톡 나에게 보내기

`talk_message` 동의항목이 있는 카카오 OAuth 토큰이 필요하다. 토큰 저장·갱신은 앱이
구현하고, 호출 시점에 유효한 액세스 토큰을 돌려주는 콜백을 넘긴다.

```python
from notifykit import KakaoMemoChannel

async def kakao_token() -> str | None:
    return await load_fresh_access_token(user_id)  # 앱의 토큰 저장소에서

channel = KakaoMemoChannel(kakao_token, link_url="https://example.com/page")
ok = await channel.send("제목", "본문")  # 텍스트 템플릿은 200자에서 잘린다
```

### 공통 인터페이스

모든 채널은 `Channel` 프로토콜(`async def send(subject, body) -> bool`)을 따른다.
새 채널이 필요하면 같은 시그니처로 구현해 추가한다.

테스트 시에는 `client=` 인자로 `httpx.AsyncClient`(예: `MockTransport` 기반)를
주입할 수 있다. 주입한 클라이언트의 수명 관리는 호출자 책임이다.
