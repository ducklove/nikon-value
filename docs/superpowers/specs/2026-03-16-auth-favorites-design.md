# OAuth 인증 + 관심 목록 기능 설계

## 개요

니콘 중고 시세 트래커(GitHub Pages 정적 사이트)에 Google/Naver/Kakao OAuth 인증과 사용자별 관심 목록 기능을 추가한다. 별도의 API 서버를 라즈베리파이에서 운영하며, 기존 정적 사이트와 CORS로 통신한다.

## 아키텍처

```
┌─────────────────────────────┐     ┌──────────────────────────────────┐
│  GitHub Pages (정적 사이트)   │     │  Raspberry Pi (API 서버)          │
│  ducklove.github.io          │     │  cantabile.tplinkdns.com         │
│                              │     │                                  │
│  index.html                  │────▶│  FastAPI + uvicorn               │
│  products/*.html             │ API │  - OAuth 엔드포인트              │
│  js/site.js (기존 유지)      │CORS │  - 관심 목록 CRUD API            │
│  js/auth.js (신규)           │     │  - SQLite (users + favorites)    │
│  css/style.css               │     │  - Let's Encrypt SSL 직접 로드   │
└─────────────────────────────┘     └──────────────────────────────────┘
```

### 핵심 원칙

- 기존 정적 사이트 변경 최소화
- API 서버 다운 시에도 기존 사이트 기능 정상 동작 (graceful degradation)
- 기존 데이터 파이프라인(GitHub Actions → fetch_prices.py → build_static_site.py) 그대로 유지
- 제품 데이터는 DB로 이전하지 않음 — API 서버는 catalog.json을 캐싱하여 사용

## OAuth 인증 흐름

```
사용자              GitHub Pages           API 서버 (FastAPI)        OAuth 제공자
  │                    │                       │                      │
  │  "구글로 로그인" 클릭                       │                      │
  │───────────────────▶│                       │                      │
  │                    │  GET /auth/google     │                      │
  │                    │──────────────────────▶│                      │
  │                    │  302 redirect URL     │                      │
  │                    │◀──────────────────────│                      │
  │  구글 로그인 페이지로 이동                                         │
  │───────────────────────────────────────────────────────────────────▶│
  │  로그인 완료, authorization code 포함하여 콜백                     │
  │◀──────────────────────────────────────────────────────────────────│
  │                    │  GET /auth/google/callback?code=xxx          │
  │                    │──────────────────────▶│                      │
  │                    │                       │  code → token 교환   │
  │                    │                       │─────────────────────▶│
  │                    │                       │  사용자 정보 반환     │
  │                    │                       │◀─────────────────────│
  │                    │                       │  DB에 사용자 upsert   │
  │                    │                       │  JWT 발급             │
  │                    │  302 → GitHub Pages + JWT (URL fragment)     │
  │                    │◀──────────────────────│                      │
  │  JWT를 localStorage에 저장                  │                      │
  │  이후 API 호출 시 Authorization 헤더 포함   │                      │
```

### 인증 세부사항

- JWT payload: `{sub: user_id, provider: "google"|"naver"|"kakao", exp: ...}`
- JWT 서명: HS256 + 서버 고유 시크릿 키
- 토큰 만료: 7일, 만료 시 재로그인 유도
- JWT는 URL fragment(`#token=xxx`)로 전달하여 서버 로그에 남지 않게 처리
- 네이버/카카오도 동일한 흐름, 엔드포인트만 `/auth/naver`, `/auth/kakao`로 분리
- 같은 사람이 다른 OAuth 제공자로 로그인하면 별도 계정으로 처리 (계정 연동은 향후 과제)

### CSRF 방지 (OAuth state 파라미터)

- OAuth 시작 시(`GET /auth/google`) 서버가 랜덤 `state` 값을 생성
- `state`는 HMAC 서명된 타임스탬프 + 복귀 URL로 구성: `HMAC(timestamp + nonce + return_to, secret)`
- 콜백에서 `state` 서명 검증 + 타임스탬프 만료(5분) 확인
- 서버 세션 없이 stateless하게 CSRF를 방지 (서명 검증만으로 충분)
- `return_to`를 포함하여 로그인 후 원래 보던 페이지(예: 제품 상세)로 복귀

### JWT 저장 방식 트레이드오프

JWT를 `localStorage`에 저장한다. 이 결정의 트레이드오프:

- **XSS 위험**: localStorage는 JS로 접근 가능하므로 XSS 시 토큰 탈취 가능
- **HttpOnly 쿠키 불가 이유**: GitHub Pages(`ducklove.github.io`)와 API 서버(`cantabile.tplinkdns.com`)가 다른 도메인이므로, 서드파티 쿠키 차단 정책(Chrome Privacy Sandbox, Safari ITP)에 의해 크로스 도메인 쿠키가 동작하지 않을 수 있음
- **수용 가능한 이유**: 관심 목록만 관리하는 비민감 서비스이며, 금전 거래나 개인 민감정보를 다루지 않음
- **완화 조치**: CDN 스크립트(Chart.js 등)에 SRI integrity 속성 추가 (build_static_site.py에서 생성 시 포함), Content-Security-Policy 헤더로 외부 스크립트 화이트리스트 적용

## 데이터베이스 스키마 (SQLite)

```sql
CREATE TABLE users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL,           -- 'google', 'naver', 'kakao'
    provider_id TEXT NOT NULL,           -- OAuth 제공자의 고유 ID
    email       TEXT,                    -- nullable (카카오는 이메일 미제공 가능)
    name        TEXT,                    -- 표시 이름
    created_at  TEXT DEFAULT (datetime('now')),
    last_login  TEXT DEFAULT (datetime('now')),
    UNIQUE(provider, provider_id)
);

CREATE TABLE favorites (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id  TEXT NOT NULL,           -- catalog의 제품 ID (예: 'nikon-z9')
    added_at    TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, product_id)
);
```

## API 엔드포인트

Base URL: `https://cantabile.tplinkdns.com`

### 인증

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/auth/google` | 구글 OAuth 시작 (302 redirect) |
| GET | `/auth/google/callback` | 구글 콜백 → JWT 발급 → 프론트로 redirect |
| GET | `/auth/naver` | 네이버 OAuth 시작 |
| GET | `/auth/naver/callback` | 네이버 콜백 |
| GET | `/auth/kakao` | 카카오 OAuth 시작 |
| GET | `/auth/kakao/callback` | 카카오 콜백 |

### 유틸리티

| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| GET | `/health` | 헬스체크 (아래 응답 형식 참조) | 불필요 |

### 사용자

| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| GET | `/api/me` | 현재 로그인 사용자 정보 | JWT 필요 |
| DELETE | `/api/me` | 회원 탈퇴 (OAuth 토큰 철회 + 계정 + 관심 목록 삭제) | JWT 필요 |

### 관심 목록

| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| GET | `/api/favorites` | 내 관심 목록 (product_id 배열) | JWT 필요 |
| PUT | `/api/favorites/{product_id}` | 관심 목록에 추가 (최대 50개) | JWT 필요 |
| DELETE | `/api/favorites/{product_id}` | 관심 목록에서 제거 | JWT 필요 |

### 응답 예시

```json
// GET /health
{
  "status": "healthy",
  "db": "ok",
  "catalog_loaded": true,
  "catalog_products": 257,
  "uptime_seconds": 86400
}

// GET /api/me
{
  "id": 1,
  "provider": "google",
  "name": "홍길동",
  "email": "hong@gmail.com"
}

// GET /api/favorites
{
  "favorites": ["nikon-z9", "nikkor-105mm-f2-5", "nikon-fm2"]
}
```

## 프론트엔드 연동

### 파일 구조 변경

```
기존 사이트 (변경 최소화)
├── js/site.js        ← 기존 코드 유지
├── js/auth.js        ← 신규: 인증 + 관심 목록 로직
└── index.html        ← 로그인 버튼 영역 추가 (빌드 스크립트에서 생성)
```

### 동작 방식

1. **페이지 로드 시** — `auth.js`가 localStorage에 JWT가 있는지 확인
   - 있으면: `/api/me` 호출로 유효성 검증 → 사용자 이름 표시 + 관심 목록 로드
   - 없으면: 로그인 버튼 표시

2. **로그인 버튼** — 헤더 우측에 "로그인" 드롭다운 (구글/네이버/카카오 선택)

3. **관심 목록 토글** — 각 제품 카드에 하트/별 아이콘 추가
   - 로그인 상태: 클릭 시 API 호출로 추가/제거
   - 비로그인 상태: 클릭 시 로그인 유도

4. **관심 목록 보기** — 카테고리 탭에 "관심 목록" 탭 추가, 선택하면 찜한 제품만 필터링

### 핵심 원칙

- 기존 `site.js`는 건드리지 않고 `auth.js`를 별도로 추가
- API 서버 URL은 `auth.js` 상단에 상수로 설정
- API 서버 다운 시에도 기존 사이트 기능은 정상 동작

## 보안

- **HTTPS 필수**: Let's Encrypt 인증서를 FastAPI(uvicorn)에서 직접 로드
- **CORS**: `ducklove.github.io` origin만 허용
- **JWT 서명**: HS256 + 서버 고유 시크릿 키 (환경 변수로 관리)
- **JWT 만료**: 7일, 갱신 엔드포인트 없이 만료 시 재로그인
- **OAuth 시크릿**: 환경 변수 또는 `.env` 파일, git에 커밋 안 함
- **SQLite**: 서버 로컬 파일, 외부 접근 불가
- **Rate limiting**:
  - 인증 엔드포인트: IP당 5req/min (sliding window)
  - API 엔드포인트: IP당 60req/min
  - slowapi 라이브러리 사용 (FastAPI 호환)
- **입력 검증**: `product_id`는 catalog에 존재하는 ID만 허용
- **관심 목록 상한**: 사용자당 최대 50개
- **Content-Security-Policy**: 외부 스크립트 화이트리스트 적용

### 에러 응답 형식

모든 에러는 다음 형식으로 통일:

```json
{
  "error": "error_code",
  "message": "사람이 읽을 수 있는 설명"
}
```

| HTTP 상태 | error 코드 | 상황 |
|-----------|-----------|------|
| 401 | `unauthorized` | JWT 없음/만료/유효하지 않음 |
| 404 | `not_found` | 존재하지 않는 product_id |
| 200 | (성공) | PUT 멱등성: 이미 존재하면 그대로 200 반환 |
| 422 | `limit_exceeded` | 관심 목록 50개 초과 |
| 429 | `rate_limited` | 요청 횟수 초과 |

## 운영

### 배포 관리

- API 서버 배포와 인증서 갱신은 저장소에 고정하지 않고 운영 환경에서 별도로 관리한다.

### catalog.json 캐싱

- 서버 시작 시 GitHub Pages URL(`https://ducklove.github.io/nikon-value/data/catalog.json`)에서 로드
- 1시간마다 백그라운드 갱신 (asyncio task)
- 갱신 실패 시 기존 캐시 유지, 에러 로그 기록
- `product_id` 검증은 캐시된 catalog의 ID 목록 기준
- 캐시 로드 전 API 요청: 502 반환 (서버 준비 중)

## 기술 스택

| 구성 요소 | 선택 | 이유 |
|-----------|------|------|
| 웹 프레임워크 | FastAPI | 프로젝트가 이미 Python 기반, 비동기 지원 |
| ASGI 서버 | uvicorn | FastAPI 표준, SSL 직접 지원 |
| OAuth 라이브러리 | Authlib | Google/Naver/Kakao 통합 지원 |
| JWT | PyJWT | 경량, 표준적 |
| 데이터베이스 | SQLite + aiosqlite | 파일 기반, RPi에 적합, 비동기 지원 |
| 환경 변수 | python-dotenv | .env 파일 로드 |

## OAuth 제공자별 사전 확인 사항

각 제공자 앱 등록 시 콜백 URL `https://cantabile.tplinkdns.com/auth/{provider}/callback` 등록 가능 여부를 구현 전에 검증해야 한다:

- **Google**: HTTPS 콜백 URL 등록 가능 여부
- **Naver**: DDNS 도메인 콜백 URL 등록 가능 여부
- **Kakao**: 동일

OAuth 콜백 URL은 기본 HTTPS URL 기준으로 등록한다. 별도 포트 포워딩은 운영 환경 설정에서 관리한다.

## 범위 외 (향후 과제)

- OAuth 계정 연동 (동일 사용자가 다른 제공자로 로그인 시 연결)
- 가격 알림 (관심 제품 가격 변동 알림)
- 관심 제품 메모 기능
- 제품 데이터 DB 이전 (파이프라인 라즈베리파이 이전 시)
- UI 정제 (로그인 버튼, 관심 목록 디자인)
- JWT refresh token / sliding expiration
- DB 마이그레이션 도구 (기능 확장 시)
