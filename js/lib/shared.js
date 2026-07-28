'use strict';

// 공용 순수 함수 모듈의 Node(CommonJS) 진입점.
//
// 왜 구현체가 여기 없는가
// ----------------------------------------------------------------------------
// 이 저장소의 프론트엔드에는 번들러가 없고, 페이지가 로드하는 <script> 태그 목록은
// nikon_value/sitegen/pages.py 가 만들고 tests/golden/*.html 이 바이트 단위로
// 고정한다. 즉 "js/lib/shared.js 를 페이지에 한 줄 더 로드"하는 선택지가 없다
// (script 태그를 추가하면 골든 테스트가 전부 깨진다).
// <script type="module"> 로 바꾸는 것도 불가 — site.js ↔ auth.js 가
// window.nikonValueCatalog / window.nikonValueAuth 전역과 defer 로드 순서에
// 의존하고 있어 결합 구조가 무너진다.
//
// 그래서 UMD 방식을 택하되, "모듈을 정의하는 파일"을 모든 페이지에서 가장 먼저
// 실행되는 js/site.js 로 잡았다.
//   - 브라우저: js/site.js 가 window.nikonValueShared 에 모듈을 붙인다.
//               js/auth.js, js/admin.js 는 그 전역 하나만 쓴다(구현 중복 0건).
//   - Node    : 이 파일이 js/site.js 를 require 하면 site.js 는 DOM이 없는 것을
//               감지해 브라우저 코드 실행을 건너뛰고 module.exports 로 같은
//               객체를 돌려준다.
//
// tests/js/*.test.js 는 항상 이 경로만 import 한다. 나중에 pages.py 에
// <script src="js/lib/shared.js"> 를 추가할 수 있게 되면, site.js 의 공용 모듈
// 블록을 이 파일로 그대로 옮기고 site.js 가 window.nikonValueShared 를 읽도록
// 바꾸기만 하면 테스트는 손댈 필요가 없다.
//
// 내보내는 함수: escapeHtml, getExchangeRate, normalizeCurrency, formatMoney,
// formatUsd, buildExchangeNote, formatRarePriceHint, filterByPeriod,
// movingAverage, buildSummedSeries, parseCompareIds, buildCompareSeries
//
// 참고: site.js는 순수 함수가 아닌 배선도 노출하지만(window.nikonValueCurrency,
// window.nikonValueChartLoader) 그건 DOM이 필요한 코드라 이 모듈에는 들어오지
// 않는다. 여기 있는 것은 전부 Node에서 그대로 테스트 가능한 순수 함수다.
module.exports = require('../site.js');
