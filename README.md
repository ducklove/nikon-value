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
- `.github/workflows/update-prices.yml`
  - 가격 데이터를 주기적으로 갱신하고, 공개 페이지 루트 파일도 함께 재생성한다.
- `.github/workflows/ci.yml`
  - push/PR마다 ruff 린트, pytest, 정적 빌드 스모크 테스트를 실행한다.
- `.github/workflows/deploy-pages.yml`
  - Pages artifact 직접 배포(수동 트리거). 루트 산출물 커밋을 없애는 마이그레이션용 준비 워크플로 —
    전환 절차는 파일 상단 주석 참고.
- `index.html`, `products/`
  - 현재 GitHub Pages가 직접 서빙하는 공개 산출물이다.

## 개발

```bash
pip install -r scripts/requirements.txt -r server/requirements.txt ruff
ruff check .       # 린트
pytest -q          # 테스트
```

공개 사이트의 API 서버 주소는 빌드 시 `NIKON_API_BASE_URL` 환경변수로 바꿀 수 있다
(페이지의 `meta[name="nikon-api-base"]`로 주입되고, `js/auth.js`가 이를 읽는다).

## 공개 사이트 빌드

```bash
python3 scripts/build_static_site.py --output dist
python3 -m http.server 8000 --directory dist
```

루트 공개 파일까지 같이 갱신하려면:

```bash
python3 scripts/build_static_site.py --output dist --publish-root
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
  - `scripts/build_static_site.py --output dist --publish-root` 실행
- `Git Push`
  - 카탈로그, 데이터, 루트 공개 파일을 함께 커밋/푸시

## 메모

- 현재 GitHub Pages는 저장소 `master` 브랜치 루트의 정적 파일을 직접 서빙한다.
- `dist/`는 검증 및 로컬 미리보기용 산출물이고, 실제 배포 파일은 루트 `index.html`, `products/`, `404.html` 등이다.
