# Nikon Value

eBay 현재 매물을 기준으로 니콘 제품 시세를 추적하는 정적 사이트와 운영 도구 모음이다.

## 구조

- `nikon_value/`
  - 수집·빌드 파이프라인 패키지. 수집은 `ebay.py`(API 클라이언트), `filters.py`(규칙 필터),
    `llm.py`(OpenRouter 보조 필터), `stats.py`(IQR 통계), `deals.py`(딜 매물 추출),
    `exchange.py`(환율), `storage.py`(JSON I/O), `fetch.py`(오케스트레이션),
    빌드는 `sitegen/` 하위 모듈로 구성된다.
- `scripts/fetch_prices.py`
  - 시세 수집 CLI 진입점 (구현은 `nikon_value/`, 기존 임포트 경로 호환 facade).
- `scripts/build_static_site.py`
  - `data/`를 읽어 GitHub Pages용 정적 산출물 `dist/`를 만들고, 필요하면 저장소 루트 공개 파일도 갱신한다
    (구현은 `nikon_value/sitegen/`).
- `js/site.js`, `js/auth.js`
  - 공개 사이트의 검색·정렬·차트 인터랙션과 로그인/관심목록 연동 로직이다.
- `css/style.css`
  - 공개 사이트 공통 스타일이다.
- `admin.html`, `js/admin.js`, `css/admin.css`, `scripts/admin_server.py`
  - 로컬에서만 쓰는 카탈로그 관리 UI와 서버다.
- `server/`
  - 라즈베리파이에서 도는 FastAPI 서버(소셜 로그인·관심목록·가격알림·텔레그램).
    정적 사이트와 별개로 배포된다 (아래 "API 서버" 절 참고).
- `deploy/`
  - API 서버 배포용 systemd 유닛.
- `.github/workflows/update-prices.yml`
  - 가격 데이터를 주기적으로 갱신해 `data/`만 커밋하고, 배포 워크플로를 체인 트리거한다.
- `.github/workflows/ci.yml`
  - push/PR마다 ruff 린트, pytest, 정적 빌드 스모크 테스트를 실행한다.
- `.github/workflows/deploy-pages.yml`
  - 빌드 산출물을 저장소에 커밋하지 않고 Pages artifact로 직접 배포한다.
    트리거 조건은 파일 상단 주석 참고.
- `dist/`
  - 빌드 산출물 디렉터리다. 저장소에 커밋하지 않으며, GitHub Pages는 이 디렉터리를
    Actions artifact로 업로드해 서빙한다 (자세한 내용은 아래 "배포" 절 참고).

## 개발

```bash
pip install -r requirements-dev.lock.txt   # 버전이 고정된 락파일
ruff check .       # 린트
pytest -q          # 테스트
```

의존성은 상위 선언(`scripts/requirements.txt`, `server/requirements.txt`,
`requirements-dev.txt`)과 버전이 고정된 락파일(`*.lock.txt`)로 나뉜다. 패키지를 추가하거나
버전을 올릴 때는 **상위 선언만 고치고 락을 재생성**한다 — 절차는
[docs/dependency-locks.md](docs/dependency-locks.md) 참고.

공개 사이트의 API 서버 주소는 빌드 시 `NIKON_API_BASE_URL` 환경변수로 바꿀 수 있다
(페이지의 `meta[name="nikon-api-base"]`로 주입되고, `js/auth.js`가 이를 읽는다).

## 공개 사이트 빌드

```bash
python3 scripts/build_static_site.py --output dist
python3 -m http.server 8000 --directory dist
```

## 로컬 관리 UI

```bash
python3 scripts/admin_server.py --port 8080
```

브라우저에서 `http://127.0.0.1:8080/admin.html`로 접속한다.

현재 admin 기본 흐름:

- `저장`
  - `config/products.yaml` 백업 후 저장
- `카테고리 수집` 또는 제품 행의 `시세 수집`
  - `scripts/fetch_prices.py --only ...` 실행
- `사이트 빌드`
  - `scripts/build_static_site.py --output dist` 실행 (로컬 미리보기·검증용)
- `Git Push`
  - 카탈로그와 데이터만 커밋/푸시 — 푸시되면 Pages 배포 워크플로가 사이트를 재배포한다

## API 서버 (라즈베리파이)

`server/`는 소셜 로그인·관심목록·가격알림·텔레그램 알림을 담당하는 별도의 FastAPI
서버로, 가정용 회선의 라즈베리파이에서 `https://cantabile.tplinkdns.com`으로 서비스된다.
정적 사이트와 수명주기가 완전히 분리되어 있어 이 서버가 죽어도 시세 페이지는 계속 뜬다.

```bash
# 로컬 실행 (server/.env 필요 — server/.env.example 참고)
uvicorn server.main:app --reload --port 8000
curl -s http://127.0.0.1:8000/health
```

설치·systemd 등록·업그레이드·롤백·**DB 백업**·장애 대응은
[docs/deploy-api-server.md](docs/deploy-api-server.md)에 정리되어 있다.
systemd 유닛은 [`deploy/nikon-value-api.service`](deploy/nikon-value-api.service)다.

> `server/data/nikon_api.db`에는 다시 만들 수 없는 사용자 데이터가 들어 있고 저장소에
> 없다. 구글 드라이브 원격 백업·복원 절차는
> [docs/backup-restore.md](docs/backup-restore.md)에 있다
> (스크립트는 [`scripts/backup_db.py`](scripts/backup_db.py), 타이머는
> [`deploy/nikon-value-backup.timer`](deploy/nikon-value-backup.timer)).

리버스 프록시 뒤에 둔 경우 `TRUSTED_PROXY_IPS`를 설정해야 rate limit이 사용자별로
갈린다. 판단 방법은 같은 문서의 "리버스 프록시와 클라이언트 IP" 절에 있다.

## 배포

- GitHub Pages는 **Actions artifact 배포**를 사용한다 (Settings → Pages → Source = GitHub Actions).
- 배포 경로: ① master로 코드가 머지되면 `deploy-pages.yml`이 자동 실행,
  ② `update-prices.yml`이 데이터를 커밋하면 같은 워크플로를 체인 트리거,
  ③ 필요 시 Actions 탭에서 수동 실행.
- 빌드 산출물(`dist/`)은 저장소에 커밋하지 않는다. `auth-complete.html`과
  `data/`(catalog.json, products/)는 빌드가 산출물에 포함한다.
