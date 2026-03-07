# Nikon Value

eBay 현재 매물을 기준으로 니콘 제품 시세를 추적하는 정적 사이트와 운영 도구 모음이다.

## 구조

- `scripts/fetch_prices.py`
  - eBay API와 Gemini 보조 필터를 사용해 `data/`를 갱신한다.
- `scripts/build_static_site.py`
  - `data/`를 읽어 GitHub Pages용 정적 산출물 `dist/`를 만들고, 필요하면 저장소 루트 공개 파일도 갱신한다.
- `js/site.js`
  - 공개 사이트에서 사용하는 검색, 정렬, 차트 인터랙션 로직이다.
- `css/style.css`
  - 공개 사이트 공통 스타일이다.
- `admin.html`, `scripts/admin_server.py`
  - 로컬에서만 쓰는 카탈로그 관리 UI와 서버다.
- `.github/workflows/update-prices.yml`
  - 가격 데이터를 주기적으로 갱신하고, 공개 페이지 루트 파일도 함께 재생성한다.
- `index.html`, `products/`
  - 현재 GitHub Pages가 직접 서빙하는 공개 산출물이다.

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

## 메모

- 현재 GitHub Pages는 저장소 `master` 브랜치 루트의 정적 파일을 직접 서빙한다.
- `dist/`는 검증 및 로컬 미리보기용 산출물이고, 실제 배포 파일은 루트 `index.html`, `products/`, `404.html` 등이다.
