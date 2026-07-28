# 텔레그램 가격 알림 운영 가이드

목표가 알림을 실제로 발송하려면 텔레그램 봇 하나를 만들어 API 서버(`server/`, 라즈베리파이)에
토큰을 넣어주면 된다. 이 문서는 운영자가 손으로 해야 하는 작업만 순서대로 정리한 것이다.

봇 토큰이 없어도 서버는 정상 기동한다. 이 경우 알림은 발송되지 않고 **대기 상태로 쌓이며**,
토큰을 넣고 재기동하면 대기 중이던 알림이 다음 점검 주기에 자동으로 발송된다
(`server/alerts.py`의 "발송 성공 시에만 triggered 갱신" 규칙 덕분이다).

---

## 1. 봇 만들기 (텔레그램 앱에서)

1. 텔레그램에서 [@BotFather](https://t.me/BotFather)를 찾아 대화를 시작한다.
2. `/newbot` 을 보낸다.
3. 봇 표시 이름을 입력한다. 예: `Nikon Value 가격 알림`
4. 봇 사용자명을 입력한다. `bot` 으로 끝나야 한다. 예: `nikon_value_alert_bot`
5. BotFather가 `123456789:AAH...` 형태의 **토큰**을 준다. 이 토큰은 봇의 비밀번호와 같다.
   - 절대 저장소에 커밋하거나 채팅·이슈에 붙여넣지 않는다.
   - 유출되었다면 BotFather에서 `/revoke` 로 즉시 폐기하고 새로 발급받는다.

선택 사항이지만 해두면 사용자 경험이 좋아지는 설정 (모두 BotFather에서):

- `/setdescription` — "Nikon Value에서 설정한 목표가에 도달하면 알려드립니다."
- `/setcommands` — 아래 두 줄을 붙여넣는다.
  ```
  start - 연동 코드를 보내 계정을 연결합니다
  unlink - 알림 연동을 해제합니다
  ```
- `/setprivacy` — 그대로 두면 된다(1:1 대화만 쓰므로 영향 없음).

## 2. 서버 환경변수 설정

`server/.env` (없으면 `server/.env.example` 복사)에 다음을 추가한다.

```
TELEGRAM_BOT_TOKEN=123456789:AAH...   # BotFather에서 받은 토큰
TELEGRAM_BOT_USERNAME=nikon_value_alert_bot   # @ 없이. 딥링크(t.me) 안내에 쓰인다
```

- `TELEGRAM_BOT_TOKEN` 이 비어 있으면 발송·수신이 모두 비활성화되고 로그에
  `TELEGRAM_BOT_TOKEN not set; Telegram polling disabled` 한 줄만 남는다.
- `TELEGRAM_BOT_USERNAME` 은 공개 값이라 비밀이 아니다. 비워두면 사용자에게 딥링크 대신
  코드만 안내한다(연동 자체는 가능).
- `.env` 파일 권한을 좁혀 두면 좋다: `chmod 600 server/.env`

## 3. 서버 재기동

```
sudo systemctl restart nikon-api    # 운영 중인 유닛 이름에 맞춰 실행
```

기동 로그에서 다음 줄을 확인한다.

```
INFO server.telegram: Telegram polling started (offset=0)
```

DB 마이그레이션(`users.telegram_chat_id` 컬럼 추가)은 기동 시 자동으로 적용된다.
기존 DB든 새 DB든 동일하게 동작하며, 여러 번 재기동해도 안전하다(멱등).
별도로 실행할 SQL은 없다.

## 4. 동작 확인

1. 사이트에서 로그인한 뒤 아무 제품 페이지의 "가격 알림" 패널을 연다.
2. "텔레그램 연동하기"를 누르면 8자리 일회용 코드가 나온다(유효 10분, 1회 사용).
3. 텔레그램에서 봇에게 그 코드를 그대로 보낸다. 딥링크 버튼을 쓰면 `/start <코드>` 가 자동 입력된다.
4. 봇이 "연동이 완료되었습니다" 라고 답하고, 웹 패널의 상태가 "텔레그램 연동됨"으로 바뀐다.
5. 목표가를 현재 중앙값보다 높게 설정하면 다음 카탈로그 갱신(최대 1시간) 때 알림이 온다.
   즉시 확인하고 싶으면 서버를 재기동한다(기동 직후 1회 점검한다).

연동 해제는 웹 패널의 "텔레그램 연동 해제" 버튼 또는 봇에게 `/unlink` 를 보내면 된다.

## 5. 수신 방식: 폴링을 쓰는 이유

봇 메시지 수신은 웹훅이 아니라 **getUpdates long polling**으로 구현했다.

- 이 서버는 가정용 회선의 라즈베리파이이고 주소가 동적 DNS(`*.tplinkdns.com`)다.
  웹훅은 텔레그램이 서버로 직접 들어오는 공인 HTTPS 엔드포인트를 요구하므로
  IP 변동·포트포워딩·인증서 갱신 중 하나만 어긋나도 연동이 조용히 끊긴다.
- 폴링은 **아웃바운드 연결만** 사용해 NAT·방화벽 뒤에서 설정 없이 동작한다.
  ISP가 CGNAT을 쓰거나 80/443 인바운드를 막아도 무관하다.
- 트래픽은 25초 long polling 기준 시간당 약 144회 요청으로, 라즈베리파이에 부담이 없다.
- 이미 `server/catalog.py` 에 있는 asyncio 백그라운드 루프 패턴을 그대로 따르므로
  운영·디버깅 방식이 기존과 같다.

처리한 마지막 `update_id`는 `app_state` 테이블에 저장하므로 서버를 재기동해도
같은 메시지를 다시 처리하지 않는다.

## 6. 문제 해결

| 증상 | 확인할 것 |
| --- | --- |
| 기동 로그에 `Telegram polling disabled` | `TELEGRAM_BOT_TOKEN` 이 `.env` 에 없거나 서버가 `.env` 를 못 읽는다 |
| `Telegram getUpdates failed: ... (retry in Ns)` 반복 | 아웃바운드 HTTPS 차단 또는 토큰 폐기. 지수 백오프로 자동 재시도한다 |
| 웹 패널에 연동 UI가 안 보임 | 서버가 구버전이거나 `/api/me/telegram` 이 401/404. 프런트는 이 경우 채널 UI를 숨긴다 |
| 코드를 보내도 "유효하지 않거나 만료" | 10분이 지났거나 이미 사용한 코드. 웹에서 새로 발급받는다 |
| 알림이 안 옴 | 사용자가 봇을 차단하면 API가 403을 돌려준다. 로그의 `error_code=403` 확인 |
| 봇 토큰이 유출된 것 같음 | BotFather `/revoke` → 새 토큰을 `.env` 에 반영 → 재기동. 사용자 연동은 유지된다 |

로그에는 봇 토큰이 절대 남지 않는다(요청 URL과 예외 메시지를 그대로 기록하지 않고
예외 타입명과 상태 코드만 남긴다). 연동 코드도 원문이 아닌 HMAC 해시로만 저장한다.

## 7. 관련 파일

| 파일 | 역할 |
| --- | --- |
| `server/telegram.py` | 발송(sendMessage), 연동 코드 발급·검증, getUpdates 폴링 루프 |
| `server/notify.py` | `send_price_alert()` — 사용자 chat_id 조회 후 발송, 성공 여부 반환 |
| `server/alerts.py` | 목표가 점검 상태 머신(발송 성공 시에만 `triggered` 갱신) |
| `server/api/telegram.py` | `GET/DELETE /api/me/telegram`, `PUT /api/me/telegram/link-code` |
| `server/database.py` | `users.telegram_chat_id` 마이그레이션, `telegram_link_codes`·`app_state` 테이블 |
| `js/auth.js` | 제품 페이지 가격 알림 패널의 연동 상태 표시·연동/해제 동선 |
