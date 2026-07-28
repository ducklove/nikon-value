# DB 백업과 복원 (구글 드라이브 원격 사본)

`/var/lib/nikon-value/nikon_api.db` 에는 **사용자 계정·관심목록·가격알림·텔레그램 연동**이
들어 있다. `.gitignore` 로 저장소에 없고, eBay 시세와 달리 **다시 수집할 수 없는 유일본**이다.
운영 환경은 가정용 회선의 라즈베리파이 한 대이고, 상태의 전부가 SD카드 한 장 위에 있다.

SD카드는 죽는다. 로컬 사본은 카드와 함께 죽으므로 백업이 아니다. 이 문서는 **원격 사본**을
만드는 자동화와, 그것을 실제로 되돌리는 절차를 다룬다.

관련 파일:

| 파일 | 역할 |
| --- | --- |
| `scripts/backup_db.py` | 스냅샷 → 검증 → 압축 → 업로드 → 보관 정리 |
| `deploy/nikon-value-backup.service` | systemd 유닛 (oneshot) |
| `deploy/nikon-value-backup.timer` | 하루 한 번 실행 |
| `deploy/nikon-value-backup.env.example` | `/etc/nikon-value-backup.env` 의 예시 |
| `tests/test_backup_db.py` | WAL 스냅샷·검증 실패·업로드 실패 회귀 테스트 |

---

## 1. 왜 `cp` 로는 안 되는가

DB는 WAL 모드(`PRAGMA journal_mode=WAL`)다. 최근 커밋이 `.db` 가 아니라 `-wal` 파일에만
존재할 수 있고, 서버가 도는 중에 `.db` 만 복사하면 **테이블조차 없는 파일**이 나온다.

실측 결과(테스트로 고정해 두었다 — `tests/test_backup_db.py`):

```
.db  = 4,096B   -wal = 140,112B     ← 501행의 커밋이 전부 WAL 에만 있다

1) cp 나이브 복사      : 열기 실패: no such table: users
2) Connection.backup() : 501행, integrity_check ok
3) VACUUM INTO         : 501행, integrity_check ok
```

`scripts/backup_db.py` 는 2번(`sqlite3.Connection.backup()`)을 쓴다. 잠금을 존중하는
온라인 백업 API 라 **서버를 멈출 필요가 없고**, 진행 중인(미커밋) 트랜잭션은 사본에
들어가지 않는다. 원본은 `mode=ro` 로 열어 백업 작업이 운영 DB를 건드릴 수 없게 못박았다.

> 사본은 마지막에 `journal_mode=DELETE` 로 되돌린다. 백업 산출물이 WAL 헤더를 달고 있으면
> 열 때마다 `-wal`/`-shm` 을 만들려 들어 다른 장비나 읽기 전용 매체에서 다루기 번거롭다.
> 복원한 DB는 서버 기동 시 `init_db()` 가 다시 WAL 로 바꾸므로 잃는 것이 없다.

---

## 2. 업로드 수단으로 rclone 을 고른 이유

후보는 셋이었다.

### 선택: rclone (외부 명령)

- **파이썬 의존성이 0개 늘어난다.** 이 저장소는 의존성을 얇게 유지해 왔다 —
  수집 파이프라인 전체가 `requests` + `PyYAML` 둘뿐이고, 설치는 해시가 박힌 락파일로 한다
  ([dependency-locks.md](dependency-locks.md)). `scripts/backup_db.py` 는 표준 라이브러리만
  쓰고, 업로드는 외부 프로세스에 위임한다. 그래서 이 스크립트는 `/usr/bin/python3` 로도
  돌고, 서버 가상환경이 깨진 상태에서도 백업을 뜰 수 있다.
- **헤드리스 인증이 정식 지원된다.** 파이에는 브라우저가 없다. `rclone authorize` 를
  데스크톱에서 실행하고 결과 토큰을 붙여넣는 절차가 공식 워크플로다(6절).
- **필요한 운영 기능이 이미 들어 있다.** 재시도, 대역 제한(`--bwlimit` — 가정용 회선의
  업로드를 백업이 다 먹지 않게 하는 데 실제로 필요하다), 체크섬 검증, `--min-age` 기반
  보관 정리. 직접 만들면 전부 우리 버그가 된다.
- **ARM 바이너리를 배포한다.** `apt install rclone` 한 줄이다.

### 탈락: Google Drive API + 서비스 계정

- **서비스 계정에는 개인 Drive 저장 용량이 없다.** 개인 구글 계정의 폴더를 공유해도
  업로드된 파일의 소유자는 서비스 계정이 되고, My Drive 업로드는 `storageQuotaExceeded`
  로 막힌다. 제대로 하려면 공유 드라이브(Workspace 유료)가 필요하다. 개인 계정 운영에는
  함정이다.
- **의존성이 10개 넘게 늘어난다.** `google-api-python-client`, `google-auth`,
  `google-auth-oauthlib`, `google-auth-httplib2` 와 그 전이 의존성(`httplib2`,
  `uritemplate`, `pyasn1*`, `rsa`, `cachetools`, `protobuf` …). 파일 하나를 다른 곳에
  옮기는 일에 이 비용을 치를 이유가 없다. 락파일 유지 비용도 영구히 따라온다.
- 서비스 계정 키(JSON)를 파이에 평문으로 두게 되는데, 그 키는 만료가 없다.

### 탈락: OAuth 데스크톱 플로우 + refresh token 직접 관리

- 토큰 갱신·만료·재인증 처리를 우리가 짜야 하고, 그 코드는 **1년에 몇 번 조용히 실패하는
  경로**라 테스트로 잡기 가장 어렵다. rclone 이 이미 하는 일이다.

### 남는 비용

rclone 은 시스템 패키지 의존성이다(파이썬 의존성이 아니다). 스크립트는 rclone 이 없으면
"rclone 이 설치돼 있는지 확인할 것"이라고 말하며 0이 아닌 코드로 죽는다. 조용히 넘어가지
않는다.

업로더는 주입 가능하다(`RcloneUploader` 는 `runner` 를 받는다). rsync/scp/다른 클라우드로
바꿔야 하면 클래스 하나를 갈아 끼우면 되고, 테스트는 네트워크도 rclone 바이너리도 쓰지 않는다.

---

## 3. 백업 주기와 보관 정책

| 항목 | 값 | 근거 |
| --- | --- | --- |
| 실행 주기 | **하루 1회** (04:20 + 최대 20분 랜덤) | 이 DB의 변경은 사람의 로그인·관심목록 조작이다. 하루치를 잃어도 손실은 "어제 즐겨찾기 몇 개"이며 재생성이 가능하다. 더 자주 돌리면 SD카드 쓰기와 가정용 회선 업로드만 늘어난다. 새벽은 사용자 활동이 가장 적은 시간이다. |
| 밀린 실행 따라잡기 | **`Persistent=true`** | 정전·재부팅으로 파이가 꺼져 있으면, 이 옵션이 없을 때 "그날 백업이 그냥 없다"는 상태가 **아무 신호 없이** 만들어진다. 조용한 결손이 이 시스템에서 가장 나쁜 실패다. 부팅 직후 회선이 아직 없을 가능성은 유닛의 `After=network-online.target` 과 `Restart=on-failure`(10분 간격, 최대 3회)가 받는다. |
| 로컬 보관 | **7일** | 로컬 사본의 용도는 "빠른 복원"과 "잘못된 마이그레이션 직후 되돌리기"다. SD카드와 운명을 같이하므로 더 길게 둘 값어치가 없다. |
| 원격 보관 | **30일** | 사고를 즉시 알아채지 못하는 경우(스키마 마이그레이션이 몇 주 뒤에 문제로 드러나는 등)를 위한 여유. 30벌을 다 합쳐도 수 MB라 늘려서 아까울 것이 없다(5절). |

두 보관 정책은 분리돼 있다(`NIKON_BACKUP_KEEP_DAYS` / `NIKON_BACKUP_REMOTE_KEEP_DAYS`).
어느 쪽이든 `0` 으로 두면 정리를 하지 않는다.

**정리 실패는 종료 코드를 바꾸지 않는다.** 정리 실패는 "사본이 더 오래 남는" 방향이라
데이터 안전 관점에서는 안전한 쪽의 실패이기 때문이다. 다만 방치하면 용량을 잠식하므로
ERROR 로그와 텔레그램 알림에 반드시 나타난다.

원격 정리는 두 겹으로 묶여 있다: `--min-age 30d` 와 `--include 'nikon_api-*.db.gz'`.
게다가 스크립트는 폴더 없는 원격 지정(`gdrive:`)을 **거부한다** — 오타 하나가 드라이브
루트 전체를 훑는 삭제 명령이 되는 것을 막기 위해서다.

---

## 4. 실패가 조용히 넘어가지 않게 만든 것들

"백업이 있다고 믿는데 실은 없는 것"이 최악이다. 그래서 다음을 전부 했다.

1. **검증한 뒤에만 업로드한다.** `PRAGMA integrity_check` 와 필수 테이블(`users`) 존재를
   확인한다. 둘 중 하나라도 실패하면 업로드하지 않고 종료 코드 2로 죽는다.
   테이블 확인이 따로 있는 이유는, 빈 DB도 무결성은 `ok` 라서 "cp 로 떠서 테이블이 통째로
   없는 파일"을 integrity_check 혼자서는 못 잡기 때문이다.
2. **업로드 종료 코드 0 을 믿지 않는다.** 올린 뒤 `rclone lsjson` 으로 되읽어 파일이
   실제로 존재하고 크기가 같은지 확인한다. 경로 오타로 엉뚱한 곳에 올라가거나 0바이트가
   올라가는 경우는 이렇게만 잡힌다.
3. **종료 코드로 표면화한다.**

   | 코드 | 뜻 |
   | --- | --- |
   | 0 | 성공 |
   | 1 | 설정 오류 (DB 경로 없음, 원격 지정 오류) |
   | 2 | 스냅샷 생성/검증 실패 — **업로드하지 않았다** |
   | 3 | 업로드 실패 또는 업로드 후 확인 실패 |

   0이 아닌 코드는 systemd 에서 unit `failed` 가 된다. `StartLimitBurst=3` 이라 계속
   실패하면 재시도를 멈추고 **failed 상태로 남아** `systemctl is-failed` 에 걸린다.
   (무한 재시도는 유닛이 절대 failed 가 되지 않게 만들어 감시를 무력화한다.)
4. **텔레그램으로 알린다.** `/etc/nikon-value-backup.env` 에 봇 토큰과 chat_id 를 넣으면
   실패 시 메시지가 온다. 실패는 항상, 성공은 `--notify-on-success` 를 줬을 때만 알린다 —
   매일 오는 "성공" 알림은 곧 아무도 안 읽는 알림이 되기 때문이다.
5. **업로드 실패한 날은 로컬 정리를 건너뛴다.** 원격에 못 올린 날 로컬 보관본까지 지우면
   사본이 한 벌도 없는 순간이 생긴다.
6. **업로드 실패해도 로컬 사본은 남긴다.** 회선이 돌아온 뒤 손으로 올릴 수 있다.

### `server/telegram.py` 를 임포트하지 않은 이유

같은 봇(= 같은 채널)을 재사용하지만 코드는 재사용하지 않는다. 표준 라이브러리
`urllib.request` 로 `sendMessage` 를 직접 호출한다. 근거:

- `server/telegram.py` 는 임포트 시점에 `server/config.py` 를 끌어오고, 거기서
  `JWT_SECRET_KEY` 가 32자 미만이면 **`RuntimeError` 로 죽는다.** 그러면 백업 작업이
  서버 설정 문제 때문에 백업을 뜨기도 전에 죽는다. 백업 도구는 백업 대상보다 고장에
  강해야 한다.
- `telegram.get_chat_id()` 는 **백업 대상 DB에서** chat_id 를 읽는다. DB가 깨진 바로 그
  순간에 알림 경로도 같이 죽는다는 뜻이다. 알림이 가장 필요한 상황에서 못 가는 설계다.
- `httpx` + `aiosqlite` + `python-dotenv` + asyncio 가 딸려 온다. 표준 라이브러리뿐이던
  스크립트가 서버 가상환경 없이는 못 도는 스크립트가 된다.
- `scripts/` 는 지금까지 `server/` 를 한 번도 임포트하지 않았다. 그 경계를 이 정도
  이득(20줄 절약)에 무너뜨릴 이유가 없다.

### 남아 있는 감시 구멍 (정직하게)

타이머가 **아예 안 도는** 경우에는 아무 알림도 오지 않는다. 알림의 부재는 정상과
구분되지 않는다. 완전한 해결은 외부 dead-man's-switch 가 필요하지만, 이 규모에는
과하다고 판단했다. 대신:

```bash
# 다음/직전 실행 시각
systemctl list-timers nikon-value-backup.timer
# 실패한 채로 남아 있지 않은지 (0 이면 정상)
systemctl is-failed nikon-value-backup.service; echo "exit=$?"
# 최근 실행 로그
journalctl -u nikon-value-backup --since "3 days ago" --no-pager
```

그리고 구글 드라이브 폴더를 열면 파일명이 곧 날짜다(`nikon_api-20260729T042000Z.db.gz`).
최신 파일 날짜만 보면 살아 있는지 즉시 안다. 도입 후 첫 일주일은
`--notify-on-success` 를 켜 두고 매일 오는지 확인한 뒤 끄는 것을 권한다.

---

## 5. 용량·비용 추정

> **산출 근거 주의:** 이 개발 환경에는 `server/data/` 가 없어 **운영 DB의 실제 크기를
> 측정하지 못했다.** 아래 값은 `server/database.py` 의 실제 스키마로 합성 DB를 만들어
> 측정한 것이다(사용자당 관심목록 10개, 가격알림 3개 가정). 운영에서 실제 값을 보려면:
>
> ```bash
> ls -l /var/lib/nikon-value/nikon_api.db*
> sudo -u nikon sqlite3 /var/lib/nikon-value/nikon_api.db \
>   "SELECT (SELECT COUNT(*) FROM users), (SELECT COUNT(*) FROM favorites), (SELECT COUNT(*) FROM price_alerts);"
> ```

측정값:

| 사용자 수 | DB 원본 | gzip 후 | 압축비 |
| --- | --- | --- | --- |
| 0 (스키마만) | 48 KB | 0.9 KB | 53x |
| 50 | 100 KB | 19 KB | 5.2x |
| 500 | 584 KB | 139 KB | 4.2x |
| 2,000 | 2.1 MB | 520 KB | 4.2x |
| 5,000 | 8.0 MB | 1.9 MB | 4.3x |

이 서비스의 현실적 규모는 수십~수백 명이다. **500명 기준 하루 139 KB** 를 기준선으로 잡는다.

| 항목 | 500명 기준 | 5,000명 기준 |
| --- | --- | --- |
| 원격 30벌 | **4.2 MB** | 57 MB |
| 로컬 7벌 | 1.0 MB | 13 MB |
| 하루 업로드 트래픽 | 139 KB | 1.9 MB |
| 하루 SD카드 쓰기 (스냅샷 + gz) | 약 0.7 MB | 약 10 MB |

**비용: 0원.** 구글 드라이브 무료 용량 15 GB(Gmail·사진과 공용) 대비 500명 기준 원격
사용량은 **0.03%**, 5,000명 기준으로도 **0.4%** 다. 이 서비스가 무료 용량을 다 쓰려면
사용자가 수십만 명이어야 한다 — 그 전에 SQLite 단일 파일 구조부터 바꿀 일이다.
유료 요금제로 넘어갈 시점은 사실상 오지 않는다.

**회선 부담도 무시할 만하다.** 하루 139 KB 는 가정용 업로드 회선에서 1초 미만이다.
그래도 백업이 회선을 독점하지 않도록 `NIKON_BACKUP_RCLONE_ARGS=--bwlimit=1M` 를 권한다.

**SD카드 수명**도 문제가 되지 않는다. 하루 0.7 MB, 1년에 약 250 MB 다. 서버 로그가 훨씬 많이 쓴다.

---

## 6. 운영자가 직접 해야 하는 것 (자동화하지 않았다)

아래 절차에는 **구글 계정 인증**이 들어간다. 자격증명은 운영자 본인만 다뤄야 하므로
이 저장소의 어떤 코드도 토큰을 발급·저장하지 않는다. 순서대로 직접 수행한다.

### 6-1. rclone 설치 (파이에서)

```bash
sudo apt update && sudo apt install -y rclone
rclone version
```

Debian/Raspberry Pi OS 저장소 버전으로 충분하다. 훨씬 최신이 필요하면
[rclone.org/downloads](https://rclone.org/downloads/) 에서 ARM 바이너리를 받되, 같은
페이지의 SHA256SUMS 로 반드시 검증한 뒤 설치한다.

### 6-2. (권장) 전용 구글 OAuth 클라이언트 ID 만들기

rclone 기본 클라이언트 ID는 전 세계 사용자가 공유해 쓰므로 구글 쪽 쿼터에 자주 걸린다.
[rclone 문서의 "Making your own client_id"](https://rclone.org/drive/#making-your-own-client-id)
를 따라 본인 계정에 클라이언트 ID/시크릿을 만들어 둔다. 5분이면 되고, 이후 원격
인증이 눈에 띄게 안정적이다.

### 6-3. 원격 만들기 — 헤드리스 인증

파이에는 브라우저가 없다. rclone 은 이 상황을 위한 절차를 제공한다.

```bash
# 설정 파일 위치를 유닛과 동일하게 맞춘 채로 실행한다.
sudo install -d -m 700 -o nikon -g nikon /var/lib/nikon-value-backup
sudo -u nikon RCLONE_CONFIG=/var/lib/nikon-value-backup/rclone.conf rclone config
```

대화형 질문에 대한 답:

| 질문 | 답 |
| --- | --- |
| `n/s/q` | `n` (New remote) |
| `name` | **`gdrive`** (`NIKON_BACKUP_REMOTE` 의 앞부분과 같아야 한다) |
| `Storage` | `drive` |
| `client_id` / `client_secret` | 6-2 에서 만든 값 (건너뛰려면 빈 값) |
| `scope` | **`3` (`drive.file`)** — 아래 설명 참고 |
| `service_account_file` | 빈 값 |
| `Edit advanced config?` | `n` |
| `Use auto config?` | **`n`** ← 이게 헤드리스 모드다 |

`n` 을 고르면 rclone 이 다음과 같은 명령을 출력한다.

```
Execute the following on the machine with the web browser (same rclone version recommended):
    rclone authorize "drive" "eyJzY29wZSI6ImRyaXZlLmZpbGUi..."
```

**브라우저가 있는 데스크톱**(맥·윈도우·리눅스, rclone 설치 필요)에서 그 명령을 그대로
실행하면 브라우저가 열리고 구글 로그인·동의 화면이 뜬다. 승인하면 터미널에
`--> {"access_token":...}` 형태의 토큰이 출력된다. 그 한 줄을 복사해 파이의 프롬프트에
붙여넣는다. 마지막에 `Configure this as a Shared Drive?` → `n`, 그리고 `y` 로 저장한다.

> **scope 는 `drive.file` 을 쓴다.** 이 범위에서 rclone 은 **자기가 만든 파일만** 보고
> 지울 수 있다. 즉 설정이 아무리 잘못돼도 운영자의 다른 구글 드라이브 파일에는 손댈 수
> 없다. 대가로, 드라이브 웹 UI 에서 백업 파일을 손으로 다른 폴더에 옮기면 rclone 이
> 그 파일을 더 이상 못 볼 수 있다. 백업 폴더는 웹에서 건드리지 말 것.

### 6-4. 폴더 만들고 확인

```bash
sudo -u nikon RCLONE_CONFIG=/var/lib/nikon-value-backup/rclone.conf \
  rclone mkdir gdrive:nikon-value-backup
sudo -u nikon RCLONE_CONFIG=/var/lib/nikon-value-backup/rclone.conf \
  rclone lsd gdrive:
sudo chmod 600 /var/lib/nikon-value-backup/rclone.conf
```

> `rclone.conf` 에는 갱신 토큰이 **평문으로** 들어간다. rclone 의 설정 암호화 기능은
> 실행할 때마다 암호를 물어보므로 무인 실행에는 쓸 수 없다. 그래서 방어선은 파일 권한
> (0600, `nikon` 소유)과 `drive.file` 범위 제한이다. 이 파일은 절대 저장소에 넣지 말 것.

### 6-5. 실패 알림용 chat_id 확인 (선택)

봇 토큰은 `server/.env` 의 `TELEGRAM_BOT_TOKEN` 을 그대로 쓰면 된다. chat_id 는 본인
계정을 봇에 연동한 뒤 DB에서 읽는 것이 가장 확실하다.

```bash
sudo -u nikon sqlite3 /var/lib/nikon-value/nikon_api.db \
  "SELECT id, email, telegram_chat_id FROM users WHERE telegram_chat_id IS NOT NULL;"
```

> `curl .../getUpdates` 로 확인하는 방법은 권하지 않는다. 서버가 getUpdates long polling
> 루프를 돌고 있어 업데이트를 먼저 가져가 버리기 때문에 빈 결과가 나오기 쉽다
> ([telegram-alerts.md](telegram-alerts.md) 참고).

### 6-6. 설정 파일과 유닛 설치

```bash
sudo cp /opt/nikon-value/deploy/nikon-value-backup.env.example /etc/nikon-value-backup.env
sudo chmod 600 /etc/nikon-value-backup.env
sudo nano /etc/nikon-value-backup.env          # 6-3 에서 정한 원격 이름/폴더를 맞춘다

sudo cp /opt/nikon-value/deploy/nikon-value-backup.service /etc/systemd/system/
sudo cp /opt/nikon-value/deploy/nikon-value-backup.timer   /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/nikon-value-backup.service
sudo systemctl daemon-reload
```

### 6-7. 첫 실행 — 드라이런부터

네트워크를 건드리지 않고 설정만 점검한다.

```bash
sudo -u nikon env $(grep -v '^#' /etc/nikon-value-backup.env | xargs) \
  /opt/nikon-value/.venv/bin/python /opt/nikon-value/scripts/backup_db.py --dry-run -v
```

```
INFO 스냅샷 검증 통과: nikon_api-20260729T042000Z.db.gz (13,189B, users 501행)
INFO [dry-run] rclone copyto /var/lib/nikon-value-backup/nikon_api-...db.gz gdrive:nikon-value-backup/nikon_api-...db.gz --checksum
```

여기까지 통과하면 진짜로 한 번 돌린다.

```bash
sudo systemctl start nikon-value-backup.service
journalctl -u nikon-value-backup -n 30 --no-pager
sudo -u nikon RCLONE_CONFIG=/var/lib/nikon-value-backup/rclone.conf \
  rclone lsl gdrive:nikon-value-backup
```

마지막으로 타이머를 켠다.

```bash
sudo systemctl enable --now nikon-value-backup.timer
systemctl list-timers nikon-value-backup.timer
```

---

## 7. 복원

**핵심: `-wal`/`-shm` 잔재를 반드시 함께 치운다.** `.db` 만 바꿔 놓고 옛 `-wal` 을 남겨
두면, SQLite 가 그 WAL 을 새 DB 위에 재생해 방금 복원한 내용을 되돌려 버리거나 깨뜨릴 수
있다. `-shm` 은 "살아 있는 WAL 이 있다"는 신호로도 작동한다. 셋은 한 세트다.

### 7-1. 복원할 백업 고르기

```bash
export RC="RCLONE_CONFIG=/var/lib/nikon-value-backup/rclone.conf"

# 원격 목록 (파일명이 곧 UTC 시각이라 정렬하면 최신이 맨 아래)
sudo -u nikon env $RC rclone lsl gdrive:nikon-value-backup | sort -k4

# 로컬에 최근 7일치가 남아 있다면 내려받을 필요도 없다
ls -l /var/lib/nikon-value-backup/*.db.gz
```

### 7-2. 내려받아 **먼저 검증한다** (아직 서비스는 그대로 둔다)

복원 디렉터리는 `nikon` 소유 0700 이라 일반 계정 셸로는 `cd` 도 안 된다. 아래는 전부
절대경로 + `sudo -u nikon` 이므로 그대로 붙여 넣으면 된다.

```bash
R=/var/lib/nikon-value-backup/restore
BK=nikon_api-20260729T042000Z.db.gz          # 7-1 에서 고른 파일 이름으로 바꾼다
DB="$R/${BK%.gz}"

sudo install -d -m 700 -o nikon -g nikon "$R"
sudo -u nikon env $RC rclone copy "gdrive:nikon-value-backup/$BK" "$R/"
sudo -u nikon gunzip -k "$R/$BK"

sudo -u nikon sqlite3 "$DB" "PRAGMA integrity_check;"
sudo -u nikon sqlite3 "$DB" "
  SELECT 'users',        COUNT(*)        FROM users
  UNION ALL SELECT 'favorites',    COUNT(*)        FROM favorites
  UNION ALL SELECT 'price_alerts', COUNT(*)        FROM price_alerts
  UNION ALL SELECT 'last_login',   MAX(last_login) FROM users;"
```

`integrity_check` 가 `ok` 이고 행 수·최근 로그인 시각이 납득되는지 **여기서** 확인한다.
이상하면 다음 단계로 가지 말고 다른 백업을 고른다. 서비스는 아직 멀쩡히 돌고 있다.

### 7-3. 서비스를 멈추고 현재 상태를 통째로 보존

```bash
sudo systemctl stop nikon-value-api

BROKEN=/var/lib/nikon-value/broken-$(date -u +%Y%m%dT%H%M%SZ)
sudo -u nikon mkdir -p "$BROKEN"
# .db, .db-wal, .db-shm 셋을 한꺼번에 옮긴다. rm 이 아니라 mv 인 이유는,
# 복원이 잘못됐을 때 되돌아갈 곳과 원인 분석 재료를 남기기 위해서다.
sudo -u nikon mv /var/lib/nikon-value/nikon_api.db* "$BROKEN"/
ls -l "$BROKEN"        # 세 파일이 다 옮겨졌는지 눈으로 확인
ls -l /var/lib/nikon-value/nikon_api.db*   # "No such file" 이어야 정상
```

> 이 `mv` 가 이 문서에서 가장 중요한 한 줄이다. `-wal`/`-shm` 이 남아 있으면 복원은
> 성공한 것처럼 보이고 데이터는 조용히 틀어진다.

### 7-4. 복원본을 제자리에

```bash
sudo install -o nikon -g nikon -m 600 "$DB" /var/lib/nikon-value/nikon_api.db
sudo systemctl start nikon-value-api
sleep 3
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

`status: healthy`, `db: ok` 를 확인하고, **실제로 로그인까지 한 번 해 본다.**
기동 시 `init_db()` 가 journal_mode 를 WAL 로 되돌리고 마이그레이션을 다시 적용한다.

### 7-5. 뒷정리

```bash
# 복원 임시 파일에는 계정 정보가 들어 있다.
sudo rm -rf /var/lib/nikon-value-backup/restore
# 며칠 지켜본 뒤, 문제가 없으면 보존해 둔 이전 상태도 지운다.
# sudo rm -rf /var/lib/nikon-value/broken-*
```

### 7-6. SD카드가 통째로 죽었을 때

1. 새 카드에 OS 설치, [deploy-api-server.md](deploy-api-server.md) 1~4절로 서버 재구축.
2. rclone 재인증 — 6-1 ~ 6-4 를 다시 한다. (구 카드의 `rclone.conf` 를 못 살렸다면
   재인증이 유일한 길이다. 그래서 이 문서를 따라 할 수 있게 써 두는 것이 곧 백업의 일부다.)
3. 7-1 ~ 7-4 로 복원. 새 카드에는 보존할 옛 DB가 없으므로 7-3 은 건너뛴다.

---

## 8. 복원 훈련 (분기 1회, 운영에 손대지 않는다)

"백업은 복원해 보기 전까지 백업이 아니다"는 문장만 문서에 넣어 두면 아무도 안 한다.
아래는 **운영 DB와 서비스를 전혀 건드리지 않고** 통째로 복사해 붙여 넣을 수 있는 절차다.
5분이면 끝난다.

```bash
set -e
export RC="RCLONE_CONFIG=/var/lib/nikon-value-backup/rclone.conf"
DRILL=$(sudo -u nikon mktemp -d /tmp/nv-drill-XXXXXX)   # nikon 소유 0700

# 1) 원격에서 가장 최근 백업 이름을 자동으로 고른다
LATEST=$(sudo -u nikon env $RC rclone lsf gdrive:nikon-value-backup \
         --include 'nikon_api-*.db.gz' | sort | tail -1)
echo "최신 원격 백업: $LATEST"

# 2) 시각을 확인한다. 25시간을 넘겼으면 타이머가 죽어 있다는 뜻이다
sudo -u nikon env $RC rclone lsl "gdrive:nikon-value-backup/$LATEST"

# 3) 내려받아 푼다
sudo -u nikon env $RC rclone copy "gdrive:nikon-value-backup/$LATEST" "$DRILL/"
sudo -u nikon gunzip "$DRILL/$LATEST"
DB="$DRILL/${LATEST%.gz}"

# 4) 진짜로 열어 본다 — 여기까지 통과해야 백업이라고 부를 수 있다
sudo -u nikon sqlite3 "$DB" "PRAGMA integrity_check;"
sudo -u nikon sqlite3 "$DB" "
  SELECT 'users',        COUNT(*)        FROM users
  UNION ALL SELECT 'favorites',    COUNT(*)        FROM favorites
  UNION ALL SELECT 'price_alerts', COUNT(*)        FROM price_alerts
  UNION ALL SELECT 'last_login',   MAX(last_login) FROM users;"

# 5) 흔적을 남기지 않는다 (계정 정보가 들어 있다)
sudo rm -rf "$DRILL"
echo "복원 훈련 통과: $LATEST"
```

기대 출력: `ok`, 그리고 운영 규모에 맞는 행 수, 최근 며칠 안의 `last_login`.
`0` 행이 나오거나 `LATEST` 가 며칠 전 날짜라면 **그것이 곧 장애 신호다.**

---

## 9. 장애 대응

| 증상 | 원인 | 조치 |
| --- | --- | --- |
| `systemctl status` 가 `failed`, 로그에 `설정 오류` | `/etc/nikon-value-backup.env` 누락/오타 | 파일 존재·권한(600) 확인. `EnvironmentFile` 에 `-` 를 안 붙여 둔 것은 의도적이다(조용히 로컬 전용이 되는 것을 막는다) |
| 로그에 `rclone 이 설치돼 있는지 확인할 것` | rclone 미설치 또는 경로 다름 | `which rclone` 후 `NIKON_BACKUP_RCLONE` 지정 |
| 로그에 `rclone copyto 실패 ... token expired` | 갱신 토큰 폐기(비밀번호 변경, 앱 권한 취소, 6개월 미사용) | 6-3 재수행. `rclone config reconnect gdrive:` 로도 된다 |
| 로그에 `원격 경로가 비어 있다` | `NIKON_BACKUP_REMOTE=gdrive:` 처럼 폴더가 없음 | 폴더까지 지정. 이 거부는 의도적이다(3절) |
| 로그에 `필수 테이블 'users' 를 읽을 수 없다` | DB가 비었거나 잘못된 경로를 백업 중 | `NIKON_BACKUP_DB_PATH` 가 `server/.env` 의 `DB_PATH` 와 같은지 확인 |
| 로그에 `integrity_check 실패` | **운영 DB가 실제로 손상됐다** | 백업이 아니라 DB 문제다. 즉시 7절로 최근 정상 백업에서 복원 |
| 로그에 `원격 파일 크기가 다르다` | 업로드 중 끊김 | 다음 실행에서 자동 재시도. 반복되면 `--bwlimit` 을 낮춘다 |
| `systemctl list-timers` 에 안 보임 | 타이머 미등록 | `sudo systemctl enable --now nikon-value-backup.timer` |
| 드라이브에 파일이 며칠째 안 늘어남 | 타이머 정지 또는 계속 실패 | `systemctl is-failed nikon-value-backup.service`, `journalctl -u nikon-value-backup --since "7 days ago"` |
| 백업 중 API 응답이 느려짐 | SD카드 I/O 경합 | 유닛에 `Nice=10`, `IOSchedulingClass=idle` 이 이미 들어 있다. 그래도 느리면 실행 시각을 더 한산한 때로 옮긴다 |

---

## 부록: 스크립트 단독 사용

```bash
# 로컬 백업만 (원격 설정 전 확인용)
python scripts/backup_db.py --db /var/lib/nikon-value/nikon_api.db \
  --out /var/lib/nikon-value-backup --no-upload

# 무엇을 할지 보기만 (네트워크·삭제 없음)
python scripts/backup_db.py --db ... --out ... --remote gdrive:nikon-value-backup --dry-run -v

# 보관 정리 끄기 (이관 작업 중 등)
python scripts/backup_db.py ... --keep-days 0 --remote-keep-days 0

# 전체 옵션
python scripts/backup_db.py --help
```

이 스크립트는 표준 라이브러리만 쓴다. 가상환경 없이 `/usr/bin/python3` 로도 돈다.
