# 0건 제품 진단

331개 제품 중 **29개가 90일 평균 매물 0.5건 미만**이고, 그중 **22개는 약 140일간
단 한 번도 매물이 잡힌 적이 없다.** 이 문서는 (1) 그 0이 어디서 나오는지에 대한
코드 수준 검증, (2) 자격증명 없이 세운 오프라인 분류, (3) 자격증명이 있는 정규
수집이 스스로 원인을 규명하도록 붙인 자동 진단을 정리한다.

기준일: 2026-07-28 수집분. 재현은 `python scripts/diagnose_empty_products.py`.

## 1. `count == 0`은 무슨 뜻인가 (코드 검증)

`fetch.process_product`의 `count`는 다음 경로를 통과한 매물 수다.

```
search_items                 # eBay Browse API
  → filter_items_with_rules  # 규칙 필터
  → filter_items_with_llm    # LLM 필터
  → collect_prices           # 가격이 있는 매물만
  → compute_stats            # count
```

이 중 두 필터에는 "전부 걸러내면 원본을 유지"하는 폴백이 있다.

| 단계 | 폴백 | 위치 |
| --- | --- | --- |
| 규칙 필터 | 결과가 비면 `items`를 그대로 반환 | `filters.py` `filter_items_with_rules` |
| LLM 필터 | 통과 인덱스가 0개면 `_keep_heuristic_set(items)` | `llm.py` |
| LLM 필터 | 호출·파싱 실패면 `items` 그대로 | `llm.py` |
| LLM 필터 | 전부 캐시 히트인데 통과가 0개여도 `_keep_heuristic_set(items)` | `llm.py` |

**따라서 비어 있지 않은 입력이 비어 있는 출력이 되는 경로는 없다.** 적응형 상한
확장(`search_items_for_product`)도 후보가 더 나을 때만 교체하므로 결과를 줄이지
않는다. 결론적으로 `count == 0`은 **eBay 검색이 0건을 반환했다**는 뜻이다.
이 성질은 `tests/test_fetch_rules.py`의 "count == 0 의 의미" 절이 고정한다.

### 반례 하나

`collect_prices`는 `price.value`가 없는 매물을 떨어뜨린다. 즉 **매물은 있는데
전부 가격이 없으면** `count == 0`이 되면서도 검색은 0건이 아니다.
`buyingOptions:{FIXED_PRICE}` 필터가 걸려 있어 실무에서는 거의 나오지 않지만
코드상 가능한 경로이므로, 진단의 `baseline` 프로브가 정규 수집과 **문자 그대로
같은 요청**을 날려 이 경우를 `not_a_search_problem` 판정으로 따로 구분한다.

### 함께 확인된 함정

연속 0건이 `ZERO_RESULT_STREAK_THRESHOLD`(3)회를 넘으면 `expand_when_empty`가
False가 되어 적응형 상한 확장이 멈춘다(`fetch.search_product_items`). 의도한
절약이지만, **실제 시세가 `max_price`보다 높은 제품은 한 번 스트릭에 걸리면
영원히 0에 갇힌다.** 그래서 프로브에 `no_price_window`를 넣었다.

## 2. 자동 진단 (프로브)

수집 중 0건이 나온 제품에 한해, 검색 제약을 **하나씩만** 푼 요청을 날려 어느
제약이 매물을 전부 걷어냈는지 기록한다. 구현은 `nikon_value/diagnostics.py`,
결과는 `data/empty-product-report.json`에 누적된다.

### 프로브 8종

| 이름 | 푸는 제약 | 읽는 방법 |
| --- | --- | --- |
| `baseline` | 없음(정규 수집과 동일) | >0이면 원인은 검색이 아니라 필터·가격 추출 |
| `no_condition` | `conditionIds:{3000}` | >0이면 Open box·Refurbished·Very Good 등으로 등록된 물건 |
| `no_delivery_country` | `deliveryCountry:KR` | >0이면 한국 미배송 셀러가 통째로 빠지고 있음 |
| `no_buying_options` | `buyingOptions:{FIXED_PRICE}` | >0이면 경매 위주 품목 |
| `no_price_window` | `price:[min..max]` | >0이면 가격창이 실제 시세와 어긋남 |
| `no_category` | `category_ids` | >0이면 카테고리 오지정 |
| `no_query_exclusions` | `q`의 `-제외어` | >0이면 제외어가 과함(키트 동봉 매물 등) |
| `core_query_only` | 모든 제약 + `q`를 모델명만 남김 | 0이면 eBay에 매물 자체가 없음(진짜 희귀) |

판정은 `summarize_probes`가 내린다: `not_a_search_problem` / `constraint_suspects`
(용의자를 매물 수 많은 순으로 나열) / `no_listings` / `incomplete`(프로브 실패).

### 예산

| 항목 | 값 | 근거 |
| --- | --- | --- |
| 프로브 대상 | 연속 0건 3회 이상 누적된 제품만 | 하루치 일시적 0건에 8회를 쓰지 않는다 |
| 제품당 프로브 | 8회, 각 1 HTTP 요청 | `limit=3`, 페이지네이션 없음 |
| 재프로브 간격 | 기본 14일, "매물 없음" 판정마다 2배 → 최대 112일 | 박물관급 제품 반복 프로브 억제 |
| 즉시 재프로브 | 설정(query·category·가격창·제외패턴) 변경 시 | 수정이 다음 수집에서 바로 검증된다 |
| 실행당 상한 | 제품 8개 (`PROBE_MAX_PRODUCTS_PER_RUN`) | |
| 하루 상한 | 제품 8개 (`PROBE_MAX_PRODUCTS_PER_DAY`) | 수집은 3시간마다 = 하루 8회 실행 |
| 끄는 법 | `python scripts/fetch_prices.py --no-diagnostics` | |

**예상 추가 호출 수**

- 최악(하루 상한을 다 쓰는 날): 8제품 × 8프로브 = **64 요청/일**.
  현재 일 호출 약 2,664회 대비 **+2.4%**.
- 첫 도입 후 약 4일이면 29개 후보를 전부 한 번씩 훑는다(29 × 8 = 232 요청).
- 정상 상태: 대부분 `no_listings`로 112일 간격까지 물러나므로
  29 × 8 ÷ 112 ≈ **2 요청/일**. 설정을 고친 날만 일시적으로 늘어난다.

프로브 호출은 `RunMetrics.ebay_diagnostic_probes`와 `ebay_http_requests`에 모두
계측되어 `data/run-metrics.json`에 남는다.

### 수집 결과와의 격리

프로브는 진단 전용이다. `catalog.json`에도 `data/products/*.json` 히스토리에도
들어가지 않으며, 실패해도 수집을 실패로 만들지 않는다(전 구간 예외 격리).
`tests/test_fetch_pipeline.py`의 `test_probes_do_not_leak_into_the_catalog_or_the_product_history`
가 이 경계를 고정한다.

## 3. 오프라인 분류 (29개)

근거는 세 가지뿐이다.

- **히스토리**: 한 번이라도 매물이 잡힌 적이 있으면 **그 제품의 설정은 동작한다.**
  질의·카테고리·가격창·필터 어디에도 치명적 오류가 없다는 강한 증거다.
- **동형 비교**: 같은 카테고리에서 정상 동작하는 제품과 설정 구조를 맞대본다.
  `category_id`는 카테고리별로 완전히 균일했고(오지정 없음), `search_category_id:
  15230`을 쓰는 28개 중 23개는 정상이다 → **카테고리 가설은 오프라인에서 기각.**
- **질의 구문**: eBay Browse API의 `q`는 공백을 AND로, `-`를 **바로 뒤 한 토큰**의
  제외로 해석한다. 공백이 OR이라면 331개 제품의 매물 수가 서로 구분되지 않을
  텐데 실제로는 0.00~443.24로 뚜렷이 갈린다 → AND 해석이 데이터로 뒷받침된다.
  같은 방향의 증거로, 제외어가 5개 이상인 53개 제품의 매물 수 중앙값은 4.04인
  반면 나머지 278개는 32.40이다.

### (a) 진짜 희귀 — 0건이 정답 (13개)

| 제품 | 90일 평균 | 매물 포착 | 근거 |
| --- | --- | --- | --- |
| `fisheye-nikkor-6mm-f28` | 0.000 | 0/142 | S+ 등급, 박물관급 ($60k~180k) |
| `zoom-nikkor-1200-1700mm` | 0.000 | 0/142 | S+ 등급, 초고가 특수 렌즈 |
| `nikkor-13mm-f56` | 0.000 | 0/142 | S+ 등급, 2023년 5만 달러 낙찰 |
| `nasa-spec-nikon-f` | 0.000 | 0/142 | S+ 등급, 사실상 별도 시장 |
| `nikon-f3h` | 0.000 | 0/142 | S 등급, 극소량 |
| `nikon-f-high-speed` | 0.000 | 0/142 | S 등급, 특수형 |
| `nikon-f3-limited` | 0.000 | 0/142 | A 등급, 한정 100대 |
| `nikon-s3m` | 0.000 | 0/136 | 195대 생산. 형제 `nikon-s3`(39.86)와 설정 동형 |
| `nikon-fisheye-camera` | 0.000 | 0/136 | 1960년 극소량 특수기 |
| `reflex-nikkor-2000mm-f11` | 0.322 | 29/142 | **간헐 포착** — 설정 정상, 공급이 얇을 뿐 |
| `nikkor-z-58mm-f095-s-noct` | 0.200 | 31/139 | **간헐 포착**, $8,000 렌즈 |
| `nikkor-z-400mm-f28-tc-vr-s` | 0.278 | 54/140 | **간헐 포착**, 최근(07-28)도 포착 |
| `nikon-f3p` | 0.333 | 33/131 | **간헐 포착**, 보도용 소량 생산 |

간헐 포착 4종은 "설정이 동작한다"가 데이터로 증명된 경우다. 이들에게는 프로브가
`no_listings`를 반복 판정하고 간격이 112일까지 물러난다.

### (b) 설정 의심 — 원인 가설 있음 (12개)

| 제품 | 90일 평균 | 가설 | 상태 |
| --- | --- | --- | --- |
| `nikon-1` | 0.000 | `q`가 `-Nikon 1 J ...` — `-`는 한 토큰만 제외하므로 **필수 토큰 `Nikon`을 스스로 제외**해 어떤 매물도 매칭 불가 | **수정함** (§4) |
| `nikon-e2-e2s` | 0.000 | `-Series E` 가 `Series` 제외 + `E` **필수**로 해석됨 | **수정함** (§4). 남은 문제: `E2`와 `E2S`를 동시에 요구 |
| `nikon-e2n-e2ns` | 0.000 | 위와 동일 | **수정함** (§4) |
| `nikon-e3-e3s` | 0.000 | 위와 동일 | **수정함** (§4) |
| `nikon-fe10` | 0.000 | `-lens -nikkor`. FE10은 렌즈 동봉 키트로 팔리는 비율이 높아 제외어가 매물을 통째로 걷어낼 수 있음. 형제 `nikon-fm10`(1.37)도 같은 구조에서 간신히 잡힘 | 프로브 `no_query_exclusions` |
| `nikon-em` | 0.489 | 위와 동일(EM은 Series E 50mm 동봉 판매가 많음). 06-12 이후 0 | 프로브 `no_query_exclusions` |
| `nikon-f3af` | 0.000 | 위와 동일. F3AF는 전용 AF 80mm 세트로 유통되는데 `-nikkor`가 이를 제외 | 프로브 `no_query_exclusions` |
| `nikon-f2t` | 0.000 | 시세가 가격창($300~2,500) 위일 가능성. 스트릭 131이라 상한 확장이 이미 멈춰 있어 자력 회복 불가 | 프로브 `no_price_window` |
| `nikkor-z-dx-50-250-f45-63-vr` | 0.000 | 현행 킷렌즈인데 0. 질의 구조가 `nikkor-z-dx-16-50-...`(141.12), `nikkor-z-dx-18-140-...`(18.74)과 **완전 동형**이라 오프라인으로는 원인을 좁힐 수 없다 | 프로브 전종 |
| `af-p-70-300-f45-63e-ed-vr` | 0.000 | 현행 렌즈인데 0. 필수 토큰 6개(`f/4.5-6.3E`, `ED`, `VR`)가 과할 가능성 | 프로브 `core_query_only` |
| `af-nikkor-28mm-f14d-ed` | 0.000 | `q`가 `f/1.4D` **와** `ED`를 동시에 요구. 이 설정의 다른 AF-D 단렌즈(35/2D, 20/2.8D, 50/1.8D…)는 어느 것도 `ED`를 붙이지 않으며, ED 표기 제품은 별도 항목 `af-s-28mm-f14e-ed`(9.00)로 존재 | 프로브 `core_query_only`. 근거가 **제품명 지식**에 기대므로 수정 보류 |
| `nikon-f2a-25th-anniversary` | 0.000 | B 등급(희귀 기념판)이라 (a)일 수도 있으나, 필수 토큰 `25th`·`Anniversary`가 판매자 표기와 어긋날 가능성 | 프로브 `core_query_only` |

### (c) 판단 보류 (4개)

| 제품 | 90일 평균 | 왜 보류인가 |
| --- | --- | --- |
| `nikon-f6` | 0.056 | F6는 희귀품이 아닌데 05-04 이후 0. 과거 32회 포착이라 설정은 동작 → 공급 문제인지 제약 문제인지 오프라인으로 못 가린다 |
| `nikkor-z-600mm-f4-tc-vr-s` | 0.000 | 03-18 이후 4개월 넘게 0. 형제 `nikkor-z-400mm-f28-tc-vr-s`는 같은 가격대에서 계속 잡힌다 |
| `nikkorex-auto-35` | 0.000 | 형제 `nikkorex-35`(7.80)·`nikkorex-35ii`(4.18)·`nikkorex-f`(5.36)와 설정 동형. 실제로 훨씬 희귀한 기종이라 (a)일 수도 있다 |
| `nikkorex-zoom-35` | 0.000 | 위와 동일 |

**합계: (a) 13 / (b) 12 / (c) 4 = 29**

## 4. 실제로 수정한 설정 (4건)

오프라인 근거만으로 **명백히** 잘못됐다고 말할 수 있는 것만 고쳤다. 두 건 모두
"자기 자신을 무효화하는 질의 구문"이고, 의도는 다른 장치가 이미 보장한다.
새 질의를 발명하지 않고 **깨진 조각을 제거**하기만 했다.

| 제품 | 전 | 후 |
| --- | --- | --- |
| `nikon-1` | `Nikon I rangefinder -Nikon 1 J -Nikon 1 V -Nikon 1 S -lens ...` | `Nikon I rangefinder -lens -nikkor -filter -hood -cap` |
| `nikon-e2-e2s` | `Nikon E2 E2S DSLR body -Series E -lens -nikkor` | `Nikon E2 E2S DSLR body -lens -nikkor` |
| `nikon-e2n-e2ns` | `Nikon E2N E2NS DSLR body -Series E -lens -nikkor` | `Nikon E2N E2NS DSLR body -lens -nikkor` |
| `nikon-e3-e3s` | `Nikon E3 E3S DSLR body -D3 -Series E -lens -nikkor` | `Nikon E3 E3S DSLR body -D3 -lens -nikkor` |

근거:

- `-`는 뒤따르는 **한 토큰**만 제외한다. `-Nikon 1 J`는 "Nikon 제외 + 1 필수 +
  J 필수"로 해석되는데, 같은 질의가 앞에서 `Nikon`을 필수로 요구한다. 즉 이
  질의는 **어떤 해석에서도 0건**이다. `Nikon 1` 미러리스 제외라는 원래 의도는
  가격창($1,000~8,000)과 `search_category_id: 15230`이 이미 보장한다.
- `-Series E`는 "Series 제외 + **E 필수**"가 된다. 한 글자 `E`를 필수로 요구하는
  것은 의도가 아니다. Series E 제외라는 의도는
  `exclude_title_patterns: ["series e"]`가 규칙 필터 단계에서 이미 보장한다.
- 이 4개는 설정 전체에서 다중 토큰 제외어를 쓰는 **유일한** 제품이고, 넷 다
  약 140일간 매물 0건이다(4/4). 나머지 327개 중 전기간 0건은 18개뿐이다.

**이 수정은 다음 수집에서 검증된다.** 설정 지문이 바뀌었으므로 진단이 간격과
무관하게 즉시 재프로브하고, 결과가 `data/empty-product-report.json`에 남는다.
`nikon-1`은 원래 박물관급(Nikon I, 약 750대)이라 수정 후에도 0건이 정답일 수
있다 — 하지만 그때의 0은 "질의가 자기모순이라서"가 아니라 "매물이 없어서"가 된다.

고치지 않은 것: 가격창, `-lens -nikkor` 제외어, `af-nikkor-28mm-f14d-ed`의 `ED`
토큰, `nikkor-z-dx-50-250`·`af-p-70-300`의 질의. 전부 실측 없이는 옳은 값을 알 수
없어 (b)로 남겼다.

## 5. 다음 수집 뒤 할 일

```bash
python scripts/diagnose_empty_products.py            # 저매물 제품 분류표
python scripts/diagnose_empty_products.py --report   # 프로브 판정과 증거 타이틀
```

`verdict`가 `constraint_suspects`면 `suspects`의 첫 항목이 가장 유력한 원인이고,
각 프로브의 `sample_titles`가 실제로 어떤 매물이 걸렸는지 보여준다. 그 증거를
근거로 `config/products.yaml`을 고치면 다음 실행이 다시 검증한다.
