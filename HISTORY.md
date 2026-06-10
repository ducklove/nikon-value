# History

## 2.1 - 2026-06-10

### Changed

- 수집·빌드 파이프라인을 `nikon_value/` 패키지로 분리 (`ebay`/`filters`/`llm`/`stats`/`exchange`/`storage`/`fetch`, `sitegen/`).
  `scripts/*.py`는 기존 임포트 경로와 호환되는 CLI facade로 유지. 빌드 산출물 바이트 동일성 검증 완료.
- `admin.html`(948줄)의 인라인 CSS/JS를 `css/admin.css`, `js/admin.js`로 분리 (동작 동일).
- 제품 페이지 Chart.js defer 로드, 카드 렌더링 DocumentFragment 일괄 삽입.
- API 서버 주소를 빌드 시 `meta[name="nikon-api-base"]`로 주입 가능하게 변경 (`NIKON_API_BASE_URL`).

### Added

- CI 워크플로 (`ci.yml`): push/PR마다 ruff 린트 + JS 문법 체크 + pytest + 정적 빌드 스모크. 데이터 봇 커밋은 제외.
- ruff 린트 설정 도입 및 코드베이스 전체 정리.
- 파이프라인 순수 함수 단위 테스트 45개 + 관심목록 제품 ID 가드 테스트 (전체 38 → 115개).
- 골든 파일 테스트: 고정 픽스처로 7개 문서를 렌더링해 바이트 단위 비교 (`tests/golden/`,
  `UPDATE_GOLDENS=1`로 재생성). 향후 템플릿 전환의 동일성 검증 장치.
- 전체 빌드 불변식 테스트 + `config/products.yaml` 스키마 검증 테스트 (ID 중복, 가격 범위,
  서브카테고리 참조, 희귀 필드 일관성).
- Pages artifact 배포 준비 워크플로 (`deploy-pages.yml`, 수동 트리거 — 루트 산출물 커밋 제거 마이그레이션용).

### Fixed

- JWT_SECRET_KEY 미설정/짧은 값으로 서버가 기동되던 문제: 32자 미만이면 기동 거부.
- 자동 수집 워크플로가 푸시 충돌 시 `rebase -X ours`로 수집 데이터를 조용히 버리던 문제:
  재시도 후 실패를 표면화해 장애 이슈가 생성되도록 변경.
- eBay 429 응답에 고정 5초 간격으로 무한 재시도하던 문제: 지수 백오프 + 최대 6회로 제한.
- 어필리에이트 헤더에 placeholder 더미 값이 전송되던 문제: `EBAY_EPN_CAMPAIGN_ID` 설정 시에만 전송.
- `auth-complete.html`이 토큰 fragment를 주소창/히스토리에 남기던 문제: 즉시 소거.
- eBay 응답의 `thumbnailImages`가 빈 배열일 때 샘플 추출이 IndexError로 죽던 문제.
- 관심목록에 카탈로그 미로드(fail-open) 상태에서 임의 문자열 ID가 저장될 수 있던 문제: 슬러그 형식 가드 추가.

## 2.0 - 2026-03-16

### Added

- Google OAuth 인증 시스템: 구글 계정으로 로그인/가입 지원.
- 사용자별 관심 목록(즐겨찾기) 기능: 제품 카드의 하트 버튼으로 추가/제거, 최대 50개 제한.
- 관심 목록 탭: 카테고리 탭에 "관심 목록" 탭 추가, 로그인 시에만 노출.
- FastAPI 기반 API 서버 (`server/`): OAuth 인증, 사용자 관리, 관심 목록 CRUD, 헬스체크 엔드포인트.
- SQLite 데이터베이스: 사용자 계정 및 관심 목록 저장 (라즈베리파이 로컬).
- JWT 토큰 기반 세션 관리 (7일 만료, localStorage).
- HMAC 서명 기반 OAuth state로 CSRF 방지.
- Rate limiting: 인증 5req/min, API 60req/min (slowapi).
- API 서버 배포 구성은 별도 운영 환경에서 관리하도록 정리.
- catalog.json 캐싱: GitHub Pages에서 1시간 주기로 갱신, 282개 제품 ID 기반 입력 검증.
- `js/auth.js`: 프론트엔드 인증 연동, MutationObserver로 동적 카드 생성 후 하트 버튼 주입.
- 회원 탈퇴 기능 (`DELETE /api/me`): 계정 + 관심 목록 cascade 삭제.

### Changed

- `scripts/build_static_site.py`: auth.js 스크립트 태그 주입, auth-area div 및 관심 목록 탭 생성.
- `css/style.css`: 로그인 UI, 드롭다운 메뉴, 하트 버튼, 관심 목록 탭 스타일 추가.
- 사이트 링크 바에 로그인 영역 통합 (시세 목록 | 참고 링크 ... 로그인).

### Architecture

- GitHub Pages (정적 사이트) + Raspberry Pi API 서버 (FastAPI) 이원 구조.
- 기존 데이터 파이프라인 (GitHub Actions → fetch_prices.py → build_static_site.py) 변경 없이 유지.
- API 서버 다운 시에도 기존 시세 조회 기능 정상 동작 (graceful degradation).

## 1.4 - 2026-03-16

### Added

- Added lens visual index grids for all three lens categories (Z-mount 41, F-mount 46, Classic 72), using eBay listing thumbnails sorted by focal length.
- Added Nikkor lens lineup hero banner image that swaps in when any lens category tab is active; camera collection hero and manual hotspots are hidden during lens view.

### Changed

- Film camera visual index boards are now wrapped in a single foldable `<details>` element (default collapsed).
- Film camera history board images are capped at native 550px width and centered, preventing blurry upscaling.
- Recalibrated image 2 hotspot row boundaries via pixel analysis (24 → 25 rows), fixing 83–122 px downward drift that misaligned hotspots from row 14 onward (35Ti, F90X, FM3A, F6 etc.).

## 1.3 - 2026-03-16

### Added

- Expanded the F-mount lens catalog with broader AF/AF-S/AF-P coverage, including wide zoom, standard zoom, tele zoom, macro, DX, and PC-E lines.
- Added Z-mount DX lenses:
  - `NIKKOR Z DX 12-28mm f/3.5-5.6 PZ VR`
  - `NIKKOR Z DX 16-50mm f/3.5-6.3 VR`
  - `NIKKOR Z DX 18-140mm f/3.5-6.3 VR`
  - `NIKKOR Z DX 24mm f/1.7`
  - `NIKKOR Z DX 50-250mm f/4.5-6.3 VR`
- Added `Nikon TC-301` to classic accessories.
- Added early rangefinder bodies `Nikon I` and `Nikon M`.
- Added a film-body visual index on the home page using the history board images in `assets/Nikon-camera-history1.jpg` and `assets/Nikon-camera-history2.jpg`.
- Added additional film-camera entries needed to support the visual index, including Nikkorex, Nikonos, Nikkormat variants, Nikon F Photomic variants, and Nikon S3M.
- Added the MIR Nikon SLR archive link to the resources page:
  - `https://www.mir.com.my/rb/photography/companies/nikon/htmls/models/htmls/slrmain8090.htm`

### Changed

- Reworked the F-mount lens category structure to make subcategories clearer and easier to browse.
- Reclassified `Nikon S3 2000 Limited` so it is no longer shown as a rare-listing watch item.
- Improved rangefinder search coverage by using the more accurate eBay Browse API search category for Nikon rangefinder bodies.
- Regenerated catalog data, per-product histories, sitemap, and static product pages to reflect the expanded catalog.

### Fixed

- Fixed the GitHub Actions publish step so generated `resources.html` is staged and pushed along with other root site files.
- Fixed a small-set Gemini filtering edge case in `scripts/fetch_prices.py` where all listings could be dropped even when the log said the original set was being accepted.
- Corrected missing or undercounted results for rangefinder models such as `Nikon M` that were being filtered out by the old search category.
