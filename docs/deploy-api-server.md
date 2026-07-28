# API 서버 배포 운영 가이드

`server/` 의 FastAPI 서버(소셜 로그인·관심목록·가격알림·텔레그램 알림)를 라즈베리파이에
올리고, 재시작하고, 망가졌을 때 되살리는 절차다. 공개 정적 사이트(GitHub Pages)와는
완전히 별개이며 이 서버가 죽어도 시세 페이지 자체는 계속 서비스된다.

전제 환경:

| 항목 | 값 |
| --- | --- |
| 공개 주소 | `https://cantabile.tplinkdns.com` (가정용 회선 + 동적 DNS) |
| 코드 위치 | `/opt/nikon-value` |
| 가상환경 | `/opt/nikon-value/.venv` |
| 실행 계정 | `nikon` (로그인 불가 시스템 계정) |
| DB | `/var/lib/nikon-value/nikon_api.db` |
| 리슨 | `127.0.0.1:8000` |

> 경로나 계정을 바꾸면 `deploy/nikon-value-api.service` 의 `WorkingDirectory`,
> `ExecStart`, `User` 도 같이 바꿔야 한다.

---

## 1. 사전 준비

```bash
sudo apt update
sudo apt install -y python3 python3-venv git
# 백업 검증에 쓰는 CLI(선택). 없어도 아래 백업 절차는 파이썬만으로 동작한다.
sudo apt install -y sqlite3
```

전용 계정과 디렉터리를 만든다.

```bash
sudo groupadd --system nikon
sudo useradd --system --gid nikon --home-dir /opt/nikon-value --shell /usr/sbin/nologin nikon
sudo mkdir -p /opt/nikon-value
sudo chown nikon:nikon /opt/nikon-value
```

`/var/lib/nikon-value` 는 systemd 의 `StateDirectory=` 가 서비스 첫 기동 때 올바른
소유자·권한(0700)으로 자동 생성하므로 직접 만들 필요가 없다.

## 2. 설치

```bash
sudo -u nikon git clone https://github.com/ducklove/nikon-value.git /opt/nikon-value
cd /opt/nikon-value
sudo -u nikon python3 -m venv .venv
sudo -u nikon .venv/bin/pip install --upgrade pip
# 상위 선언(server/requirements.txt)이 아니라 고정된 락파일을 설치한다.
# 해시가 들어 있어 pip 가 자동으로 hash-checking 모드로 검증한다.
sudo -u nikon .venv/bin/pip install -r server/requirements.lock.txt
```

락파일을 쓰는 이유와 갱신 절차는 [docs/dependency-locks.md](dependency-locks.md) 참고.

## 3. `.env` 구성

```bash
sudo -u nikon cp /opt/nikon-value/server/.env.example /opt/nikon-value/server/.env
sudo -u nikon chmod 600 /opt/nikon-value/server/.env
sudo -u nikon nano /opt/nikon-value/server/.env
```

`server/config.py` 가 python-dotenv 로 **절대경로** `/opt/nikon-value/server/.env` 를 읽는다.
systemd 의 `EnvironmentFile=` 은 쓰지 않는다 — 파서를 하나로 유지해야 따옴표 처리 차이로
값이 어긋나는 사고가 없다.

반드시 채워야 하는 값:

| 키 | 비고 |
| --- | --- |
| `JWT_SECRET_KEY` | 32자 이상. `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `GOOGLE_/NAVER_/KAKAO_CLIENT_ID`·`SECRET` | 각 provider 콘솔에서 발급 |
| `DB_PATH` | **`/var/lib/nikon-value/nikon_api.db` (절대경로 필수)** |

`DB_PATH` 를 반드시 절대경로로 적는다. `.env.example` 의 `./data/nikon_api.db` 는
`WorkingDirectory` 기준 상대경로라, systemd 로 띄우면 저장소 안(`/opt/nikon-value/data/`)을
가리키게 되고 `ProtectSystem=strict` 때문에 쓰기가 막혀 기동이 실패한다.

`TELEGRAM_BOT_TOKEN` 은 선택이다. 비워 두면 서버는 정상 기동하고 알림만 대기 상태로 남는다.
`TRUSTED_PROXY_IPS` 는 6절을 읽고 정한다.

### 기존 DB 이관

이미 `server/data/nikon_api.db` 로 운영 중이었다면, 서버를 멈춘 뒤 옮긴다.

```bash
sudo systemctl stop nikon-value-api          # 아직 등록 전이면 생략
sudo mkdir -p /var/lib/nikon-value
sudo -u nikon /opt/nikon-value/.venv/bin/python - <<'PY'
import sqlite3
src = sqlite3.connect("/opt/nikon-value/server/data/nikon_api.db")
dst = sqlite3.connect("/var/lib/nikon-value/nikon_api.db")
src.backup(dst)          # WAL 을 포함해 일관된 사본을 만든다
dst.close(); src.close()
PY
sudo chown -R nikon:nikon /var/lib/nikon-value
sudo chmod 700 /var/lib/nikon-value
```

`cp` 로 옮기지 말 것. 이유는 7절에 있다.

## 4. systemd 등록

```bash
sudo cp /opt/nikon-value/deploy/nikon-value-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nikon-value-api      # enable = 재부팅 시 자동 시작
```

`systemd-analyze verify /etc/systemd/system/nikon-value-api.service` 로 문법을 확인할 수 있다.

## 5. 기동 / 재시작 / 로그

```bash
sudo systemctl status nikon-value-api        # 현재 상태
sudo systemctl restart nikon-value-api       # 설정(.env) 변경 반영은 restart 로만
sudo systemctl stop nikon-value-api
journalctl -u nikon-value-api -f             # 실시간 로그
journalctl -u nikon-value-api -n 200 --no-pager
journalctl -u nikon-value-api --since "1 hour ago" -p err
```

`systemctl reload` 는 일부러 지원하지 않는다. uvicorn 이 SIGHUP 을 처리하지 않아
서비스가 조용히 내려가기 때문이다.

### 헬스체크

```bash
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

```json
{ "status": "healthy", "db": "ok", "catalog_loaded": true,
  "catalog_products": 331, "uptime_seconds": 42 }
```

- `status` 가 `degraded` 면 DB 연결이 깨진 것이다 (8절 참고).
- `catalog_loaded` 가 `false` 면 기동 시 카탈로그 HTTP 요청이 실패한 것이다. 서버는
  계속 뜨지만 **가격 알림이 동작하지 않는다.** 1시간 주기 갱신에서 자동 복구된다.
- 바깥에서도 확인: `curl -s https://cantabile.tplinkdns.com/health`
  (실패하면 서버가 아니라 동적 DNS·포트포워딩·프록시 쪽을 먼저 의심한다.)

---

## 6. 리버스 프록시와 클라이언트 IP

rate limit(OAuth 5회/분 등)은 클라이언트 IP를 버킷 키로 쓴다. 리버스 프록시 뒤에서
이 값을 잘못 잡으면 두 가지 반대 방향의 사고가 난다.

- **전부 한 버킷** — 모든 요청의 TCP 피어가 프록시 IP 하나라, "서버 전체가 합쳐서
  5회/분"이 되어 정상 사용자가 로그인하다 차단된다.
- **제한 무력화** — `X-Forwarded-For` 를 무조건 믿으면 아무나 헤더를 위조해 요청마다
  다른 키를 만들어 제한을 통째로 우회한다.

그래서 이 서버는 **신뢰할 프록시를 명시했을 때만** forwarded 헤더를 읽는다.

### 내 배포가 어느 쪽인지 확인하기

진단용 엔드포인트가 있다. 서버 안에서, 그리고 **바깥 회선(휴대폰 LTE 등)에서** 각각 호출한다.

```bash
# 파이 안에서
curl -s http://127.0.0.1:8000/health/client-ip | python3 -m json.tool
# 바깥에서 (실제 사용자 경로)
curl -s https://cantabile.tplinkdns.com/health/client-ip
```

```json
{ "client_ip": "203.0.113.7", "peer_ip": "127.0.0.1",
  "x_forwarded_for": "203.0.113.7", "x_real_ip": null,
  "trust_proxy_headers": true }
```

판독법:

| 바깥에서 본 결과 | 해석 | 조치 |
| --- | --- | --- |
| `client_ip` 가 내 공인 IP와 같다 | 정상. 사용자별로 버킷이 갈린다 | 없음 |
| `client_ip` 가 `127.0.0.1`/사설 IP 로 고정 | **프록시 뒤인데 헤더를 못 읽고 있다.** 전체가 한 버킷 | `TRUSTED_PROXY_IPS` 설정 |
| `x_forwarded_for` 가 `null` 인데 `peer_ip` 가 내 공인 IP | 프록시가 없는 직접 노출 배포 | 그대로 두기(설정 불필요) |
| `x_forwarded_for` 는 있는데 `trust_proxy_headers` 가 `false` | 프록시는 있으나 미설정 | `TRUSTED_PROXY_IPS` 설정 |

여러 회선(집 WiFi / LTE)에서 호출했을 때 `client_ip` 가 **같게** 나오면 공유 버킷 상태다.

로그로도 보인다. 기동 시 다음 중 하나가 journal 에 찍힌다.

```
rate limit: TRUSTED_PROXY_IPS 미설정 — forwarded 헤더를 무시하고 TCP 피어 주소를 키로 쓴다.
rate limit: 신뢰 프록시를 거친 요청에 한해 X-Forwarded-For/X-Real-IP 를 클라이언트 IP 로 쓴다 (TRUSTED_PROXY_IPS=127.0.0.1).
```

### 설정

프록시가 **있을 때만** `server/.env` 에 프록시 주소를 적는다.

```ini
# 같은 파이에서 nginx 가 127.0.0.1:8000 으로 넘기는 경우
TRUSTED_PROXY_IPS=127.0.0.1
# 사내망의 별도 프록시 장비 / 대역
# TRUSTED_PROXY_IPS=192.168.0.10,192.168.0.11
# TRUSTED_PROXY_IPS=192.168.0.0/24
```

비워 두면(기본값) 헤더를 전혀 신뢰하지 않고 TCP 피어 주소를 그대로 쓴다. 프록시가 없는
배포에서는 이게 정답이며, 위조 헤더가 아무 영향도 주지 못한다.

**와일드카드(`*`)는 지원하지 않는다.** 모든 피어를 신뢰하면 헤더가 곧 위조 가능한 입력이
되기 때문이다. 잘못된 값은 경고 로그만 남기고 무시되어 "신뢰하지 않음"으로 떨어진다.

nginx 쪽은 표준 헤더를 넘기도록 해 둔다.

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

`$proxy_add_x_forwarded_for` 는 nginx 가 실제로 본 주소를 체인 **오른쪽**에 덧붙인다.
서버는 체인을 오른쪽부터 훑어 처음 만나는 비신뢰 주소를 클라이언트로 잡으므로,
클라이언트가 미리 심어 둔 위조 항목은 항상 왼쪽에 남아 무시된다.

### uvicorn `--proxy-headers` 와의 관계

uvicorn 에도 같은 기능(`ProxyHeadersMiddleware`)이 있다. 주의할 점은 **기본으로 켜져
있고 `--forwarded-allow-ips` 기본값이 `127.0.0.1`** 이라는 것이다. 즉 아무 설정도 하지
않아도 uvicorn 은 루프백에서 온 요청의 `X-Forwarded-For` 를 말없이 신뢰한다. 같은 파이의
nginx 구성이라면 그 덕에 대체로 잘 동작하지만, 동시에 파이 안의 아무 프로세스나
클라이언트 IP를 위장할 수 있다는 뜻이기도 하다.

그래서 유닛 파일은 **`--no-proxy-headers` 로 uvicorn 쪽을 끄고**, 판정 주체를
`TRUSTED_PROXY_IPS` 하나로 모은다. 근거:

- rate limit 키는 보안 통제인데, 그 정확성이 CLI 플래그에만 달려 있으면 실행 방법이
  바뀌는 순간(다른 ASGI 서버, `python -m`, 컨테이너) 조용히 무너진다.
- CLI 플래그는 테스트로 고정할 수 없다. 앱 레벨 로직은 위조 시나리오까지
  `tests/test_rate_limit_client_ip.py` 로 회귀 방지된다.
- uvicorn 은 `X-Real-IP` 를 보지 않는다.
- 끄더라도 잃는 기능이 없다. uvicorn 미들웨어의 나머지 역할은 `X-Forwarded-Proto` 로
  `request.url` 의 스킴을 고치는 것인데, 이 앱은 요청 URL을 전혀 읽지 않는다
  (OAuth redirect_uri 와 프런트 리다이렉트는 모두 `API_BASE_URL`/`FRONTEND_URL` 설정에서 온다).

둘을 같이 켜도 결과는 어긋나지 않는다. uvicorn 이 먼저 `client` 를 실제 주소로 바꾸면
앱은 그 주소를 "신뢰 목록에 없는 피어"로 보고 그대로 키에 쓴다 — 답이 같다. 다만
설정이 두 군데로 갈리므로 권장하지 않는다.

**절대 금지: `--forwarded-allow-ips '*'`.** 이 값을 주면 uvicorn 은 XFF 체인의 **맨 앞**
값을 그대로 클라이언트로 삼아, 원격 공격자가 헤더 하나로 임의의 IP를 위장할 수 있다.
`TRUSTED_PROXY_IPS` 를 올바로 설정해도 구제되지 않는다 — 앱이 받는 피어 주소 자체가
이미 위조된 값이기 때문이다.

---

## 7. DB 백업 (가장 중요)

`/var/lib/nikon-value/nikon_api.db` 에는 **사용자 계정·관심목록·가격알림·텔레그램 연동**이
들어 있다. `.gitignore` 로 저장소에 없고, eBay 시세와 달리 **다시 수집할 수 없는 유일본**이다.
이 파일이 날아가면 모든 사용자가 로그인 상태와 관심목록을 잃는다.

### `cp` 로 백업하면 안 되는 이유

DB는 WAL 모드(`PRAGMA journal_mode=WAL`)라 최근 커밋이 `-wal` 파일에만 있을 수 있다.
서버가 도는 중에 `.db` 만 복사하면 **테이블조차 없는 파일**이 나온다. 실제로 확인한 결과:

```
1) cp 나이브 복사      : 열기 실패: no such table: users
2) Connection.backup() : 501행, integrity_check ok
3) VACUUM INTO         : 501행, integrity_check ok
```

### 올바른 백업

서버를 멈추지 않고 일관된 사본을 만든다. 파이썬 표준 라이브러리만 쓰므로 추가 설치가 없다.

```bash
sudo -u nikon /opt/nikon-value/.venv/bin/python - <<'PY'
import sqlite3, datetime, pathlib
out = pathlib.Path("/var/backups/nikon-value")
out.mkdir(parents=True, exist_ok=True)
stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
target = out / f"nikon_api-{stamp}.db"
src = sqlite3.connect("file:/var/lib/nikon-value/nikon_api.db?mode=ro", uri=True)
dst = sqlite3.connect(target)
src.backup(dst)              # 온라인 백업 API: 잠금을 존중하며 WAL 내용까지 포함
dst.close(); src.close()
print(target)
PY
```

`sqlite3` CLI 가 있다면 아래도 동등하다.

```bash
sudo -u nikon sqlite3 /var/lib/nikon-value/nikon_api.db \
  ".backup '/var/backups/nikon-value/nikon_api-$(date +%Y%m%d-%H%M%S).db'"
```

### 자동화 (systemd timer)

`/etc/systemd/system/nikon-value-backup.service` 와 `.timer` 를 만든다. 위 파이썬 블록을
`/opt/nikon-value/deploy/` 밖의 스크립트(예: `/usr/local/bin/nikon-value-backup`)로 저장하고:

```ini
# nikon-value-backup.service
[Service]
Type=oneshot
User=nikon
ExecStart=/usr/local/bin/nikon-value-backup

# nikon-value-backup.timer
[Timer]
OnCalendar=daily
Persistent=true
[Install]
WantedBy=timers.target
```

```bash
sudo systemctl enable --now nikon-value-backup.timer
sudo systemctl list-timers nikon-value-backup.timer
```

### 지켜야 할 것

- **보관 주기**: 최소 7일치. 오래된 파일 정리 —
  `find /var/backups/nikon-value -name '*.db' -mtime +14 -delete`
- **원격 사본**: 같은 SD카드에만 두면 카드가 죽을 때 백업도 같이 죽는다. 최소 한 벌은
  다른 장비나 클라우드로 `rsync`/`rclone` 한다. **저장소에 커밋하지 말 것**(개인정보).
- **복원 훈련**: 백업은 복원해 보기 전까지 백업이 아니다. 분기에 한 번:
  ```bash
  sqlite3 /var/backups/nikon-value/<최신>.db "PRAGMA integrity_check; SELECT COUNT(*) FROM users;"
  ```
- 권한: 백업 디렉터리도 `chmod 700`. 계정 정보가 들어 있다.

### 복원

```bash
sudo systemctl stop nikon-value-api
sudo -u nikon cp /var/lib/nikon-value/nikon_api.db /var/lib/nikon-value/nikon_api.db.broken
sudo rm -f /var/lib/nikon-value/nikon_api.db-wal /var/lib/nikon-value/nikon_api.db-shm
sudo -u nikon cp /var/backups/nikon-value/<복원할파일>.db /var/lib/nikon-value/nikon_api.db
sudo systemctl start nikon-value-api
curl -s http://127.0.0.1:8000/health
```

멈춘 상태에서는 `cp` 가 안전하다(위 경고는 "돌아가는 중 복사"에 대한 것이다).
다만 `-wal`/`-shm` 잔재를 반드시 지워야 옛 WAL 이 새 DB 에 덧씌워지지 않는다.

---

## 8. 업그레이드

```bash
# 0) 먼저 백업 (7절)
sudo -u nikon /opt/nikon-value/.venv/bin/python - <<'PY'
... # 위 백업 블록
PY

# 1) 되돌릴 지점을 기록해 둔다
cd /opt/nikon-value && git rev-parse --short HEAD | sudo -u nikon tee /var/lib/nikon-value/last-good-commit

# 2) 코드/의존성 갱신
sudo -u nikon git pull
sudo -u nikon /opt/nikon-value/.venv/bin/pip install -r server/requirements.lock.txt

# 3) 재시작 + 검증
sudo systemctl restart nikon-value-api
sleep 3
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
journalctl -u nikon-value-api -n 50 --no-pager
```

`status: healthy`, `db: ok`, `catalog_loaded: true` 를 모두 확인한 뒤에 끝낸다.
로그인까지 실제로 한 번 해 보는 것이 가장 확실하다.

DB 스키마는 기동 시 `init_db()` 가 마이그레이션한다. **되돌릴 수 없는 변경일 수 있으므로
0번 백업을 건너뛰지 말 것.**

## 9. 롤백

```bash
sudo systemctl stop nikon-value-api
cd /opt/nikon-value
sudo -u nikon git checkout "$(cat /var/lib/nikon-value/last-good-commit)"
sudo -u nikon /opt/nikon-value/.venv/bin/pip install -r server/requirements.lock.txt
sudo systemctl start nikon-value-api
curl -s http://127.0.0.1:8000/health
```

락파일을 함께 되돌리므로 의존성 버전도 그때 상태로 정확히 복원된다.
스키마가 바뀌었던 업그레이드라면 코드만 되돌려서는 부족하고, 7절의 복원 절차로
DB 도 같이 되돌려야 한다.

유닛 파일 자체를 바꿨다면 `sudo cp` 후 `sudo systemctl daemon-reload` 를 잊지 말 것.

## 10. 장애 대응

| 증상 | 원인 | 조치 |
| --- | --- | --- |
| `systemctl status` 가 `failed`, 로그에 `JWT_SECRET_KEY must be...` | `.env` 누락/경로 오류, 키 32자 미만 | `/opt/nikon-value/server/.env` 존재·권한(600·`nikon` 소유) 확인, 키 재생성 후 restart |
| 로그에 `ModuleNotFoundError: No module named 'server'` | `WorkingDirectory` 가 저장소 루트가 아님 | 유닛의 `WorkingDirectory=/opt/nikon-value` 확인 후 `daemon-reload` + restart |
| 로그에 `sqlite3.OperationalError: unable to open database file` | `DB_PATH` 가 상대경로거나 `ProtectSystem=strict` 로 막힌 위치 | `DB_PATH` 를 `/var/lib/nikon-value/...` 절대경로로. 그 밖의 경로면 유닛에 `ReadWritePaths=` 추가 |
| `/health` 가 `db: error` | DB 파일 손상 또는 권한 | `sqlite3 <db> "PRAGMA integrity_check"`. 깨졌으면 7절 복원 |
| `/health` 의 `catalog_loaded: false` 가 계속 유지 | GitHub Pages 접근 불가(회선/DNS), `CATALOG_URL` 오타 | `curl -sI $CATALOG_URL`. 알림은 카탈로그가 로드돼야 동작한다 |
| 로그인 시도가 곧바로 `429 Too Many Requests` | 프록시 뒤인데 클라이언트 IP를 못 잡아 전체가 한 버킷 | 6절 진단 후 `TRUSTED_PROXY_IPS` 설정 → restart |
| 재시작이 반복되다 멈춤(`start-limit-hit`) | 설정 오류로 5분 내 5회 실패 | 원인 수정 후 `sudo systemctl reset-failed nikon-value-api && sudo systemctl start nikon-value-api` |
| 바깥에서만 접속 불가, 안에서는 정상 | 동적 DNS 갱신 실패, 포트포워딩, 프록시/인증서 | `dig +short cantabile.tplinkdns.com` 과 현재 공인 IP 비교, 공유기 설정 확인 |
| 텔레그램 알림만 안 옴 | `TELEGRAM_BOT_TOKEN` 미설정/폐기 | 서버는 정상이며 알림만 대기 상태다. [telegram-alerts.md](telegram-alerts.md) 참고 |
| SD카드 불량으로 부팅 불가 | 하드웨어 | 새 카드에 OS 설치 → 1~4절 재실행 → 7절로 DB 복원. **원격 백업이 없으면 여기서 데이터가 끝난다** |

일단 상태부터 보고 싶을 때:

```bash
systemctl status nikon-value-api --no-pager -l
journalctl -u nikon-value-api -n 100 --no-pager
curl -s http://127.0.0.1:8000/health
df -h /              # 디스크 가득 참도 DB 오류의 흔한 원인이다
```

---

## 부록: Docker 를 쓰지 않는 이유

이 배포에는 systemd 로 충분하다고 판단했다.

- **인스턴스가 하나뿐이다.** 단일 파이에서 단일 프로세스를 돌린다. 오케스트레이션,
  스케일링, 무중단 롤링 배포가 필요 없다.
- **재현성은 이미 락파일이 담당한다.** 컨테이너 이미지의 주된 이점인 "같은 버전이 깔린다"는
  `server/requirements.lock.txt` 의 해시 고정으로 확보된다.
- **격리는 systemd 가 제공한다.** `ProtectSystem=strict`, `NoNewPrivileges`, `PrivateTmp`,
  `CapabilityBoundingSet=`, `SystemCallFilter` 로 컨테이너에 준하는 커널 수준 격리를
  추가 런타임 없이 얻는다.
- **비용이 실제로 든다.** 라즈베리파이에서 Docker 데몬은 상시 메모리를 잡아먹고,
  ARM 이미지 빌드가 느리며, SD카드 쓰기가 늘어 수명을 깎는다.
- **SQLite 파일이 상태의 전부다.** 볼륨 마운트라는 층을 하나 더 얹으면 백업 경로가
  헷갈려 오히려 사고 위험이 커진다.

여러 대로 늘리거나 다른 서비스와 함께 배치해야 할 때 다시 검토하면 된다.
