# OAuth 인증 + 관심 목록 기능 설계

## 개요

니콘 중고 시세 트래커(GitHub Pages 정적 사이트)에 Google/Naver/Kakao OAuth 인증과 사용자별 관심 목록 기능을 추가한다. 별도의 API 서버를 라즈베리파이에서 운영하며, 기존 정적 사이트와 CORS로 통신한다.

## 아키텍처

```
┌─────────────────────────────┐     ┌──────────────────────────────────┐
│  GitHub Pages (정적 사이트)   │     │  Raspberry Pi (API 서버)          │
│  ducklove.github.io          │     │  cantabile.tplinkdns.com:3380    │
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

Base URL: `https://cantabile.tplinkdns.com:3380`

### 인증

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/auth/google` | 구글 OAuth 시작 (302 redirect) |
| GET | `/auth/google/callback` | 구글 콜백 → JWT 발급 → 프론트로 redirect |
| GET | `/auth/naver` | 네이버 OAuth 시작 |
| GET | `/auth/naver/callback` | 네이버 콜백 |
| GET | `/auth/kakao` | 카카오 OAuth 시작 |
| GET | `/auth/kakao/callback` | 카카오 콜백 |

### 사용자

| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| GET | `/api/me` | 현재 로그인 사용자 정보 | JWT 필요 |

### 관심 목록

| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| GET | `/api/favorites` | 내 관심 목록 (product_id 배열) | JWT 필요 |
| PUT | `/api/favorites/{product_id}` | 관심 목록에 추가 | JWT 필요 |
| DELETE | `/api/favorites/{product_id}` | 관심 목록에서 제거 | JWT 필요 |

### 응답 예시

```json
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
- **Rate limiting**: 인증 엔드포인트에 기본적인 속도 제한
- **입력 검증**: `product_id`는 catalog에 존재하는 ID만 허용

## 기술 스택

| 구성 요소 | 선택 | 이유 |
|-----------|------|------|
| 웹 프레임워크 | FastAPI | 프로젝트가 이미 Python 기반, 비동기 지원 |
| ASGI 서버 | uvicorn | FastAPI 표준, SSL 직접 지원 |
| OAuth 라이브러리 | Authlib | Google/Naver/Kakao 통합 지원 |
| JWT | PyJWT | 경량, 표준적 |
| 데이터베이스 | SQLite + aiosqlite | 파일 기반, RPi에 적합, 비동기 지원 |
| 환경 변수 | python-dotenv | .env 파일 로드 |

## 범위 외 (향후 과제)

- OAuth 계정 연동 (동일 사용자가 다른 제공자로 로그인 시 연결)
- 가격 알림 (관심 제품 가격 변동 알림)
- 관심 제품 메모 기능
- 제품 데이터 DB 이전 (파이프라인 라즈베리파이 이전 시)
- UI 정제 (로그인 버튼, 관심 목록 디자인)
