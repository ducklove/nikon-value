# Nikon Value

eBay 현재 매물을 기준으로 니콘 제품 시세를 추적하는 정적 사이트와 운영 도구 모음이다.

## 구조

- `scripts/fetch_prices.py`
  - eBay API와 Gemini 보조 필터를 사용해 `data/`를 갱신한다.
- `scripts/build_static_site.py`
  - `data/`를 읽어 GitHub Pages용 정적 산출물 `dist/`를 만든다.
- `js/site.js`
  - 공개 사이트에서 사용하는 검색, 정렬, 차트 인터랙션 로직이다.
- `css/style.css`
  - 공개 사이트 공통 스타일이다.
- `admin.html`, `scripts/admin_server.py`
  - 로컬에서만 쓰는 카탈로그 관리 UI와 서버다.
- `.github/workflows/update-prices.yml`
  - 가격 데이터를 주기적으로 갱신한다.
- `.github/workflows/deploy-pages.yml`
  - `dist/`를 GitHub Pages로 배포한다.

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

## 메모

- 공개용 GitHub Pages는 루트 HTML이 아니라 `dist/` 산출물을 배포한다.
- 루트의 예전 `index.html`, `product.html`, `js/app.js`, `js/product.js`는 더 이상 사용하지 않는다.
