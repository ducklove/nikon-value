(function () {
  'use strict';

  // ===========================================================================
  // 공용 순수 함수 모듈 (UMD)
  // ---------------------------------------------------------------------------
  // 이 저장소에는 번들러가 없고, 페이지가 로드하는 <script> 태그 목록은
  // nikon_value/sitegen/pages.py 와 바이트 단위로 고정된 tests/golden/*.html 이
  // 함께 소유한다. 즉 "새 JS 파일을 페이지에 한 줄 더 로드"하는 선택지가 없다.
  // 그래서 모든 페이지에서 가장 먼저 실행되는 site.js가 모듈 정의를 맡고,
  // 뒤이어 로드되는 auth.js(그리고 admin.html의 admin.js)는 window.nikonValueShared
  // 전역으로 같은 구현 하나만 쓴다. 기존 window.nikonValueCatalog /
  // window.nikonValueAuth 결합 방식과 동일한 패턴이라 로드 순서 보장도 그대로다.
  //
  // Node(단위 테스트)에서는 js/lib/shared.js 가 이 파일을 require 해서
  // module.exports 로 같은 객체를 받는다. DOM이 없는 환경에서는 아래 export 직후
  // 곧바로 return 하므로 브라우저 전용 코드는 실행되지 않는다.
  // ===========================================================================

  // HTML 이스케이프 — 텍스트/속성값 삽입 지점 모두에서 안전해야 하므로
  // & < > " ' 다섯 문자를 전부 치환한다. 과거에는 site.js(정규식, " 처리)와
  // auth.js/admin.js(DOM textContent 기반, " 미처리)가 따로 있었는데,
  // DOM 버전은 title="..." / value="..." 같은 속성 삽입 지점에서 따옴표를
  // 빠져나갈 수 있어 XSS 위험이 있었다. 정규식 버전으로 통일한다.
  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function getExchangeRate(exchangeData) {
    const raw = Number(exchangeData?.rate);
    return Number.isFinite(raw) && raw > 0 ? raw : null;
  }

  function normalizeCurrency(value, exchangeData) {
    if (value === 'krw' && getExchangeRate(exchangeData)) return 'krw';
    return 'usd';
  }

  function formatMoney(value, options) {
    const { currency = 'usd', exchangeData = null, signDisplay = 'auto' } = options || {};
    if (value === null || value === undefined || value === '') return '-';

    const amount = Number(value);
    if (!Number.isFinite(amount)) return '-';

    let converted = amount;
    let symbol = '$';
    let locale = 'en-US';
    let formatterOptions = {};

    if (currency === 'krw') {
      const rate = getExchangeRate(exchangeData);
      if (!rate) return '-';
      converted = Math.round(amount * rate);
      symbol = '₩';
      locale = 'ko-KR';
    } else if (!Number.isInteger(amount)) {
      formatterOptions = {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      };
    }

    const absValue = Math.abs(converted);
    const formatted = `${symbol}${absValue.toLocaleString(locale, formatterOptions)}`;
    if (converted < 0) return `-${formatted}`;
    if (signDisplay === 'always' && converted > 0) return `+${formatted}`;
    return formatted;
  }

  // 관심 목록 대시보드 전용 USD 표기(정수 반올림). formatMoney와 달리 통화 토글을
  // 타지 않는 자리에서만 쓴다.
  function formatUsd(value) {
    if (value == null || isNaN(value)) return '-';
    return '$' + Math.round(value).toLocaleString('en-US');
  }

  function buildExchangeNote(exchangeData) {
    const rate = getExchangeRate(exchangeData);
    if (!rate) return 'KRW 환산용 환율 데이터를 불러오지 못했습니다.';
    const source = exchangeData?.source || '환율 데이터';
    const referenceDate = exchangeData?.reference_date || '-';
    return `USD 1 = KRW ${rate.toLocaleString('ko-KR', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })} (${source} ${referenceDate} 기준)`;
  }

  function formatRarePriceHint(value) {
    if (value === null || value === undefined || value === '') return '공개 표본 부족';
    const text = String(value).trim();
    if (!text || text.includes('$')) return text;
    return /^[0-9,+.\-\u2013\s]+$/.test(text) ? text + '$' : text;
  }

  function getReferenceDate(data) {
    if (!data.length) return new Date();
    return new Date(data[data.length - 1].date + 'T00:00:00Z');
  }

  // days=0(또는 falsy)은 "전체 기간". 기준일은 마지막 데이터 포인트이고,
  // 경계 계산은 UTC로만 한다(로컬 타임존이 하루를 밀지 않도록).
  function filterByPeriod(data, days) {
    if (!days || data.length === 0) return data;
    const cutoff = getReferenceDate(data);
    cutoff.setUTCDate(cutoff.getUTCDate() - days);
    const cutoffStr = cutoff.toISOString().split('T')[0];
    return data.filter((entry) => entry.date >= cutoffStr);
  }

  function movingAverage(data, windowDays) {
    // 날짜 기준 윈도우 평균: 수집 공백이 있어도 달력상 최근 windowDays일만 묶는다.
    return data.map((entry, idx) => {
      const end = new Date(entry.date + 'T00:00:00Z');
      const start = new Date(end);
      start.setUTCDate(start.getUTCDate() - (windowDays - 1));
      const startStr = start.toISOString().split('T')[0];
      let sum = 0;
      let count = 0;
      for (let i = idx; i >= 0; i -= 1) {
        if (data[i].date < startStr) break;
        if (data[i].median != null) {
          sum += data[i].median;
          count += 1;
        }
      }
      return count ? Math.round((sum / count) * 100) / 100 : null;
    });
  }

  // 관심 목록 대시보드의 합산 시계열.
  //
  // 예전 규칙과 왜 바꿨나
  // ---------------------------------------------------------------------------
  // 과거에는 "모든 제품이 값을 가진 날짜"만 남겼다(known === maps.length). 그래서
  // 관심목록에 어제 추가된 신제품이 하나만 있어도 1년치 추이가 이틀로 잘렸다 —
  // 가장 늦게 시작한 제품 하나가 전체 구간을 결정하는 구조였다.
  //
  // 그렇다고 "값이 하나라도 있으면 포함"으로만 바꾸면 더 나쁘다. 신규 제품의 첫
  // 관측일에 합산액이 계단처럼 뛰어서, 시세가 오른 것과 목록이 늘어난 것을
  // 구분할 수 없게 된다. 그래서 구간을 살리되 포인트마다 구성을 함께 내보낸다.
  //
  //   total  : 그날 값이 확보된 제품들의 forward-fill 합계 (절대액)
  //   known  : total에 실제로 들어간 제품 수
  //   missing: 아직 첫 관측이 오지 않아 total에서 빠진 제품 수
  //   index  : 연쇄(chain-linked) 지수. 첫 포인트 = 100.
  //
  // index는 "그날과 그 전날 모두 값이 있던 제품들"의 변동률만 이어 붙인다. 새로
  // 합류한 제품은 합류 당일의 변동률 계산에서 빠지므로 지수에는 계단이 생기지
  // 않는다(주가지수의 제수 조정과 같은 방식). 즉 index는 "구성 변화를 걷어낸
  // 시세 변동", total은 "그 시점 구성에서의 절대액"이고 둘의 의미가 다르다.
  // 그 차이를 UI가 설명할 수 있도록 known/missing을 같이 돌려준다
  // (문구 생성은 buildSummedSeriesNote 참고).
  //
  // forward-fill 때문에 known은 단조 증가한다 — 한 번 값이 잡힌 제품은 이후
  // 날짜에서 다시 빠지지 않는다. 아래 소비자들(점선 구간 계산, 안내 문구)이 이
  // 성질에 기대고 있다.
  function buildSummedSeries(histories) {
    var maps = (histories || []).map(function (entries) {
      var map = {};
      (entries || []).forEach(function (e) {
        if (e && e.median != null) map[e.date] = e.median;
      });
      return map;
    });
    var dateSet = {};
    maps.forEach(function (map) {
      Object.keys(map).forEach(function (d) { dateSet[d] = true; });
    });
    var dates = Object.keys(dateSet).sort();
    var last = maps.map(function () { return null; });
    var prev = null; // 직전 포인트의 forward-fill 스냅샷 (연쇄 지수 계산용)
    var rawIndex = 100;
    var series = [];
    dates.forEach(function (date) {
      var known = 0;
      var sum = 0;
      for (var i = 0; i < maps.length; i++) {
        if (maps[i][date] != null) last[i] = maps[i][date];
        if (last[i] != null) { known++; sum += last[i]; }
      }
      // 아직 아무 제품도 시작하지 않은 날은 그릴 값이 없다.
      if (known === 0) return;

      if (prev !== null) {
        // 양쪽 날짜에 모두 값이 있는 제품만으로 비율을 낸다. 오늘 처음 합류한
        // 제품은 분자/분모 어디에도 들어가지 않으므로 지수가 튀지 않는다.
        var base = 0;
        var curr = 0;
        for (var j = 0; j < maps.length; j++) {
          if (prev[j] == null || last[j] == null) continue;
          base += prev[j];
          curr += last[j];
        }
        // base가 0이면(전부 median 0) 비율을 정의할 수 없어 직전 지수를 유지한다.
        if (base > 0) rawIndex = rawIndex * (curr / base);
      }

      series.push({
        date: date,
        total: sum,
        known: known,
        missing: maps.length - known,
        index: Math.round(rawIndex * 100) / 100,
      });
      prev = last.slice();
    });
    return series;
  }

  // buildSummedSeries 결과를 사용자에게 설명하는 안내 문구.
  //
  // 여기 있는 이유: 합산 절대액의 의미가 구간마다 달라진다는 사실을 정직하게
  // 말하는 것이 이 대시보드의 핵심이라, 문구가 실제 데이터 상태와 어긋나지
  // 않도록 순수 함수로 두고 테스트로 고정한다(buildExchangeNote와 같은 패턴).
  // options.mode: 'indexed'(기본) | 'absolute' — 지금 화면에 보이는 축을 말한다.
  function buildSummedSeriesNote(series, options) {
    if (!series || !series.length) return '추이를 그릴 데이터가 아직 부족합니다.';

    var indexed = (options || {}).mode !== 'absolute';
    var first = series[0];
    var last = series[series.length - 1];
    var productCount = first.known + first.missing;
    var changes = 0;
    var fullFrom = null;
    for (var i = 0; i < series.length; i++) {
      if (i > 0 && series[i].known !== series[i - 1].known) changes += 1;
      if (fullFrom === null && series[i].missing === 0) fullFrom = series[i].date;
    }

    var parts = [
      '히스토리가 있는 ' + productCount + '개 제품의 중앙값 합산 추이입니다 (' +
      first.date + ' ~ ' + last.date + ').',
    ];

    if (changes === 0 && first.missing === 0) {
      parts.push('전 구간에서 ' + productCount + '개 모두 시세 기록이 있어 구성 변화가 없고, 합산액을 그대로 컬렉션 가치로 읽어도 됩니다.');
      return parts.join(' ');
    }

    if (changes === 0) {
      // 시세 기록이 아예 잡히지 않는 제품이 섞인 경우(중앙값이 전부 null 등).
      parts.push('그중 ' + first.missing + '개는 시세 기록이 없어 전 구간에서 합산에 빠져 있습니다 — 표시된 금액은 ' + first.known + '개 기준입니다(전 구간 점선).');
    } else {
      parts.push(
        '시세 기록이 늦게 시작한 제품이 있어 ' + first.date + '에는 ' + first.known +
        '개만 합산되고 이후 ' + changes + '번에 걸쳐 ' + last.known + '개까지 늘어납니다. ' +
        '제품이 합류하는 날(차트의 주황 점)마다 합산액이 계단처럼 뛰므로, 구성이 다른 구간의 금액끼리는 비교할 수 없습니다.'
      );
      parts.push(
        fullFrom
          ? '점선은 일부만 합산된 구간, 실선은 ' + fullFrom + '부터 ' + productCount + '개가 모두 반영된 구간입니다.'
          : '아직 ' + last.missing + '개는 시세 기록이 없어 마지막 날도 ' + last.known + '개 기준입니다(전 구간 점선).'
      );
    }

    parts.push(
      indexed
        ? '지금 보는 지수(첫날=100)는 그날 함께 관측된 제품들의 변동률만 이어 붙여 계산하므로 합류로 인한 점프가 없습니다. 절대 금액은 위 합산 카드와 각 점의 툴팁에서 볼 수 있습니다.'
        : '지금 보는 합산액에는 이 계단이 그대로 들어 있습니다. 시세 변동만 보려면 지수(첫날=100) 보기로 바꾸세요.'
    );
    return parts.join(' ');
  }

  // URL 파라미터(?ids=a,b,c)는 신뢰할 수 없는 입력이다. 존재하지 않는 ID, 과다한
  // 개수, 비정상적으로 긴 문자열이 그대로 들어올 수 있으므로 화면에 닿기 전에
  // 여기서 전부 정규화한다.
  //   - 문자열이 아니면 빈 배열
  //   - 길이/조각 수에 상한을 둬서 병적으로 긴 입력에 시간을 쓰지 않는다
  //   - knownIds에 없는 ID는 버린다 (fetch 시도 자체를 하지 않는다)
  //   - 중복은 첫 등장만 남긴다
  //   - 최종적으로 max개까지만 남긴다
  // 버려진 값은 rejected로 돌려줘 사용자에게 이유를 알릴 수 있게 한다.
  var COMPARE_IDS_MAX_LENGTH = 2000;
  var COMPARE_IDS_MAX_SEGMENTS = 50;

  function parseCompareIds(raw, knownIds, max) {
    var limit = Number.isFinite(max) && max > 0 ? Math.floor(max) : 5;
    var result = { ids: [], unknown: [], duplicated: 0, overflow: 0, truncated: false };
    if (typeof raw !== 'string' || raw === '') return result;

    var text = raw;
    if (text.length > COMPARE_IDS_MAX_LENGTH) {
      text = text.slice(0, COMPARE_IDS_MAX_LENGTH);
      result.truncated = true;
    }

    var known = knownIds instanceof Set ? knownIds : new Set(knownIds || []);
    var segments = text.split(',');
    if (segments.length > COMPARE_IDS_MAX_SEGMENTS) {
      segments = segments.slice(0, COMPARE_IDS_MAX_SEGMENTS);
      result.truncated = true;
    }

    var seen = new Set();
    segments.forEach(function (segment) {
      var id = String(segment).trim();
      if (!id) return;
      if (!known.has(id)) {
        // 알 수 없는 ID는 하나만 모아 보여 주면 되므로 중복은 접는다.
        if (result.unknown.indexOf(id) === -1 && result.unknown.length < 5) result.unknown.push(id);
        return;
      }
      if (seen.has(id)) { result.duplicated += 1; return; }
      seen.add(id);
      if (result.ids.length >= limit) { result.overflow += 1; return; }
      result.ids.push(id);
    });

    return result;
  }

  // 제품별 히스토리를 하나의 차트 좌표계로 정규화한다.
  //   items : [{ id, label, history: [{date, median, count, ...}] }]
  //   metric: 'median' | 'count'  (count는 0도 유효한 값이라 null과 구분한다)
  //   mode  : 'absolute' | 'indexed' (indexed는 각 시리즈 첫 유효값을 100으로)
  // 날짜가 겹치지 않는 제품들을 합치므로 labels는 합집합이고, 관측이 없는 날은
  // null로 남겨 Chart.js가 선을 끊거나(spanGaps) 이어 그릴 수 있게 한다.
  // 같은 날짜가 두 번 들어간 히스토리(수집 재실행)는 마지막 값만 쓴다.
  function buildCompareSeries(items, options) {
    var opts = options || {};
    var metric = opts.metric === 'count' ? 'count' : 'median';
    var indexed = opts.mode === 'indexed';

    var prepared = [];
    var dateSet = {};
    (items || []).forEach(function (item) {
      if (!item || !Array.isArray(item.history)) return;
      var map = {};
      item.history.forEach(function (entry) {
        if (!entry || typeof entry.date !== 'string' || !entry.date) return;
        var value = entry[metric];
        if (metric === 'count') value = value == null ? 0 : value;
        if (value == null) return;
        var num = Number(value);
        if (!Number.isFinite(num)) return;
        map[entry.date] = num;
      });
      var dates = Object.keys(map);
      if (!dates.length) return;
      dates.forEach(function (d) { dateSet[d] = true; });
      prepared.push({ id: item.id, label: item.label, map: map });
    });

    var labels = Object.keys(dateSet).sort();
    var series = prepared.map(function (item) {
      var base = null;
      var data = labels.map(function (date) {
        var value = Object.prototype.hasOwnProperty.call(item.map, date) ? item.map[date] : null;
        if (value == null) return null;
        if (!indexed) return value;
        // 0은 지수화 기준이 될 수 없다(0으로 나눌 수 없다). 매물 수처럼 0에서
        // 시작하는 시리즈는 처음으로 0이 아닌 값을 기준(=100)으로 잡고,
        // 그 이전 구간은 null로 남긴다.
        if (!base) {
          if (!value) return null;
          base = value;
        }
        return Math.round((value / base) * 1000) / 10;
      });
      var valid = data.filter(function (v) { return v != null; });
      return {
        id: item.id,
        label: item.label,
        data: data,
        first: valid.length ? valid[0] : null,
        last: valid.length ? valid[valid.length - 1] : null,
        points: valid.length,
      };
    }).filter(function (item) { return item.points > 0; });

    return { labels: labels, series: series };
  }

  const shared = {
    escapeHtml,
    getExchangeRate,
    normalizeCurrency,
    formatMoney,
    formatUsd,
    buildExchangeNote,
    formatRarePriceHint,
    filterByPeriod,
    movingAverage,
    buildSummedSeries,
    buildSummedSeriesNote,
    parseCompareIds,
    buildCompareSeries,
  };

  if (typeof window !== 'undefined') window.nikonValueShared = shared;
  if (typeof module === 'object' && module.exports) module.exports = shared;

  const CURRENCY_STORAGE_KEY = 'nikon-value-currency';

  // DOM이 없으면(Node 단위 테스트) 여기서 끝. 아래는 전부 브라우저 전용이다.
  if (typeof document === 'undefined') return;

  function readJsonScript(id, fallback) {
    const node = document.getElementById(id);
    if (!node || !node.textContent) return fallback;
    try {
      return JSON.parse(node.textContent);
    } catch (err) {
      console.error('Failed to parse JSON payload:', err);
      return fallback;
    }
  }

  function getInitialCurrency(params, exchangeData) {
    const requested =
      params.get('currency') ||
      window.localStorage.getItem(CURRENCY_STORAGE_KEY) ||
      'usd';
    return normalizeCurrency(requested, exchangeData);
  }

  function saveCurrency(currency) {
    window.localStorage.setItem(CURRENCY_STORAGE_KEY, currency);
  }

  function applyMoneyElements(root, currency, exchangeData) {
    root.querySelectorAll('[data-money-usd]').forEach((node) => {
      const amount = Number(node.dataset.moneyUsd);
      const signDisplay = node.dataset.moneySign || 'auto';
      node.textContent = formatMoney(amount, {
        currency,
        exchangeData,
        signDisplay,
      });
    });
  }

  function syncCurrencyButtons(buttons, currency, exchangeData) {
    const krwAvailable = Boolean(getExchangeRate(exchangeData));
    buttons.forEach((button) => {
      const mode = button.dataset.currency || 'usd';
      if (mode === 'krw') button.disabled = !krwAvailable;
      button.classList.toggle('is-active', mode === currency);
      button.setAttribute('aria-pressed', mode === currency ? 'true' : 'false');
    });
  }

  function updateExchangeNotes(root, exchangeData) {
    const text = buildExchangeNote(exchangeData);
    root.querySelectorAll('[data-exchange-note]').forEach((node) => {
      node.textContent = text;
    });
  }

  // 통화 토글 배선. 순수 함수가 아니라서 nikonValueShared(순수 함수 전용)에는
  // 넣지 않고, 별도 전역으로 노출한다. js/compare.js가 홈·제품 페이지와 똑같은
  // 토글 동작을 구현 중복 없이 쓰기 위한 것이다 —
  // window.nikonValueCatalog / window.nikonValueAuth 와 같은 결합 패턴이다.
  window.nikonValueCurrency = {
    storageKey: CURRENCY_STORAGE_KEY,
    getInitial: getInitialCurrency,
    save: saveCurrency,
    applyMoney: applyMoneyElements,
    syncButtons: syncCurrencyButtons,
    updateNotes: updateExchangeNotes,
  };

  // Chart.js 온디맨드 로더. 제품 페이지는 차트가 페이지의 본문이라 <script defer>로
  // 정적 로드하지만, 관심목록 대시보드와 비교 페이지는 "그릴 데이터가 실제로
  // 생겼을 때"만 필요하다. 같은 구현을 auth.js와 compare.js가 공유한다.
  const CHART_CDN = 'https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js';
  let chartLoadPromise = null;

  function ensureChartJs() {
    if (window.Chart) return Promise.resolve(true);
    if (chartLoadPromise) return chartLoadPromise;
    chartLoadPromise = new Promise((resolve) => {
      const script = document.createElement('script');
      script.src = CHART_CDN;
      script.onload = () => resolve(true);
      script.onerror = () => {
        // 다음 호출에서 다시 시도할 수 있게 캐시를 비운다.
        chartLoadPromise = null;
        resolve(false);
      };
      document.head.appendChild(script);
    });
    return chartLoadPromise;
  }

  window.nikonValueChartLoader = { cdn: CHART_CDN, ensure: ensureChartJs };

  function initHeroEasterEgg() {
    const toggles = Array.from(document.querySelectorAll('[data-hero-easter-egg="negative"]'));
    if (!toggles.length) return;

    toggles.forEach((toggle) => {
      const banner = toggle.closest('.hero-banner');
      if (!banner) return;

      function syncPressedState() {
        const isActive = banner.classList.contains('hero-banner--negative');
        toggle.setAttribute('aria-pressed', isActive ? 'true' : 'false');
      }

      toggle.addEventListener('click', () => {
        banner.classList.toggle('hero-banner--negative');
        syncPressedState();
      });

      syncPressedState();
    });
  }

  function createProductCard(d) {
    var a = document.createElement('a');
    a.className = 'product-card';
    a.href = 'products/' + d.id + '.html';
    a.dataset.productId = d.id;
    a.dataset.categoryId = d.category_id;
    a.dataset.search = d.search;
    a.dataset.nameKo = d.name_ko;
    a.dataset.median = d.median != null ? String(d.median) : '';
    a.dataset.count = String(d.count || 0);
    a.dataset.releaseYear = String(d.release_year || 0);
    a.dataset.priority = String(d.priority || 0);
    a.dataset.featureOrder = String(d.feature_order);

    var thumb = d.thumb
      ? '<img class="product-card__thumb" src="' + escapeHtml(d.thumb) + '" alt="' + escapeHtml(d.name_en) + '" loading="lazy">'
      : '<div class="product-card__thumb-placeholder" aria-hidden="true">Nikon</div>';

    var badges = '';
    if (d.badge) badges += '<span class="product-card__badge">' + escapeHtml(d.badge) + '</span>';
    if (d.at_low) badges += '<span class="product-card__badge product-card__badge--low">1년 최저</span>';
    if (d.is_rare) {
      var rl = ('희귀 ' + (d.rarity_tier || '')).trim();
      badges += '<span class="product-card__badge product-card__badge--rare">' + escapeHtml(rl) + '</span>';
    }
    var badgeHtml = badges ? '<div class="product-card__badges">' + badges + '</div>' : '';

    var priceHtml;
    if (d.median != null) {
      priceHtml = '<div class="product-card__price"><span class="money-value" data-money-usd="' + d.median + '" data-money-sign="auto">' + formatMoney(d.median) + '</span></div>';
    } else {
      priceHtml = '<div class="product-card__price product-card__price--na">데이터 없음</div>';
    }

    var trendHtml = '';
    if (d.delta_pct != null) {
      var isUp = d.delta_pct > 0;
      var isDown = d.delta_pct < 0;
      var arrow = isUp ? '&#9650;' : (isDown ? '&#9660;' : '&#8212;');
      var trendClass = 'product-card__trend';
      if (isUp) trendClass += ' product-card__trend--up';
      else if (isDown) trendClass += ' product-card__trend--down';
      var pctText = (isUp ? '+' : '') + d.delta_pct.toFixed(1) + '%';
      trendHtml = '<span class="' + trendClass + '">' + arrow + ' ' + pctText + '</span>';
    }

    var rangeHtml = '';
    if (d.q1 != null && d.q3 != null) {
      rangeHtml = '<div class="product-card__range">Q1-Q3: <span class="money-range">' +
        '<span class="money-value" data-money-usd="' + d.q1 + '" data-money-sign="auto">' + formatMoney(d.q1) + '</span>' +
        ' - ' +
        '<span class="money-value" data-money-usd="' + d.q3 + '" data-money-sign="auto">' + formatMoney(d.q3) + '</span>' +
        '</span></div>';
    }

    // 유동성 칩은 희소 신호가 있을 때만 붙는다(전체의 약 11%). 나머지 카드에
    // '풍부'를 붙이면 정보가 아니라 노이즈라서 서버가 scarcity=null로 내려보낸다.
    // 근거는 nikon_value/sitegen/liquidity.py 주석 참고.
    var scarcityHtml = '';
    if (d.scarcity && d.scarcity.grade) {
      var s = d.scarcity;
      var detail = s.days + '일 중 ' + s.zero_pct + '%가 매물 0건, 평균 ' + s.avg + '개';
      scarcityHtml = '<span class="product-card__liquidity" title="' + escapeHtml(detail) + '">' +
        '<span class="visually-hidden">유동성 </span>' + escapeHtml(s.grade) + '</span>';
    }

    a.innerHTML = thumb +
      '<div class="product-card__body">' +
        '<div class="product-card__header">' +
          '<div class="product-card__name">' + escapeHtml(d.name_ko) + '</div>' +
          badgeHtml +
        '</div>' +
        '<div class="product-card__name-en">' + escapeHtml(d.name_en) + '</div>' +
        '<div class="product-card__taxonomy">' + escapeHtml(d.category_label) + '</div>' +
        priceHtml +
        trendHtml +
        '<div class="product-card__meta"><span>현재 매물 ' + (d.count || 0) + '개</span>' + scarcityHtml + '</div>' +
        rangeHtml +
      '</div>';
    return a;
  }

  function createRareWatchCard(d) {
    var a = document.createElement('a');
    a.className = 'rare-watch-card';
    a.href = 'products/' + d.id + '.html';
    a.dataset.categoryId = d.category_id;
    a.dataset.search = d.search;
    a.innerHTML =
      '<div class="rare-watch-card__top">' +
        '<span class="rare-watch-card__tier">' + escapeHtml(d.rarity_tier || '희귀') + '</span>' +
        '<span class="rare-watch-card__count">현재 매물 ' + (d.count || 0) + '개</span>' +
      '</div>' +
      '<strong>' + escapeHtml(d.name_ko) + '</strong>' +
      '<div class="rare-watch-card__name-en">' + escapeHtml(d.name_en) + '</div>' +
      '<div class="rare-watch-card__taxonomy">' + escapeHtml(d.category_label) + '</div>' +
      '<div class="rare-watch-card__price">현재 중앙값 <span class="money-value" data-money-usd="' + (d.median != null ? d.median : '') + '" data-money-sign="auto">' + formatMoney(d.median) + '</span></div>' +
      '<div class="rare-watch-card__hint">최근 희귀 시세 ' + escapeHtml(formatRarePriceHint(d.rarity_price_hint)) + '</div>' +
      '<p class="rare-watch-card__note">' + escapeHtml(d.rarity_note || '개별 상태 확인 필요') + '</p>';
    return a;
  }

  function initCatalogPage() {
    var grid = document.getElementById('product-grid');
    var cardsData = readJsonScript('cards-data', []);
    var rareWatchGrid = document.getElementById('rare-watch-grid');
    var rareWatch = document.getElementById('rare-watch');
    var rareWatchSummary = document.getElementById('rare-watch-summary');

    // Clear skeleton placeholders
    grid.innerHTML = '';

    // Render product cards from JSON data (fragment에 모아 reflow 1회로 삽입)
    var cardsFragment = document.createDocumentFragment();
    var cards = cardsData.map(function (d) {
      var el = createProductCard(d);
      cardsFragment.appendChild(el);
      return el;
    });
    grid.appendChild(cardsFragment);

    // Render rare watch cards
    var rareCardsData = cardsData
      .filter(function (c) { return c.is_rare && c.count > 0; })
      .sort(function (a, b) {
        return (-(a.rarity_sort || 0) + (b.rarity_sort || 0)) ||
               (-(a.median || 0) + (b.median || 0)) ||
               ((a.count || 0) - (b.count || 0)) ||
               (a.name_ko || '').localeCompare(b.name_ko || '', 'ko');
      });
    var rareFragment = document.createDocumentFragment();
    var rareCards = rareCardsData.map(function (d) {
      var el = createRareWatchCard(d);
      rareFragment.appendChild(el);
      return el;
    });
    if (rareWatchGrid) rareWatchGrid.appendChild(rareFragment);
    if (rareWatch && rareCards.length > 0) rareWatch.hidden = false;

    const tabs = Array.from(document.querySelectorAll('.category-tab[data-category-id]'));
    const searchInput = document.getElementById('search-input');
    const sortSelect = document.getElementById('sort-select');
    const visibleCount = document.getElementById('visible-count');
    const contextLabel = document.getElementById('catalog-context');
    const emptyState = document.getElementById('catalog-empty');
    const filmAtlas = document.getElementById('film-atlas');
    const dealRadar = document.getElementById('deal-radar');
    const lensAtlases = Array.from(document.querySelectorAll('.lens-atlas[data-category-id]'));
    const heroPictureDefault = document.getElementById('hero-picture-default');
    const heroImageLens = document.getElementById('hero-image-lens');
    const heroHotspots = document.querySelector('.hero-hotspots');
    const lensCategories = new Set(['z-mount-lenses', 'f-mount-lenses', 'classic-lenses']);
    const currencyButtons = Array.from(document.querySelectorAll('.currency-toggle__button[data-currency]'));
    const exchangeData = readJsonScript('exchange-rate-data', {});
    const params = new URLSearchParams(window.location.search);

    let activeCategory = params.get('category') || 'all';
    let searchTerm = params.get('q') || '';
    let sortMode = params.get('sort') || 'featured';
    let currencyMode = getInitialCurrency(params, exchangeData);

    if (searchInput) searchInput.value = searchTerm;
    if (sortSelect) sortSelect.value = sortMode;

    function getNumber(card, key, fallback) {
      const raw = card.dataset[key];
      if (raw === undefined || raw === '') return fallback;
      const value = Number(raw);
      return Number.isFinite(value) ? value : fallback;
    }

    function compareCards(a, b) {
      switch (sortMode) {
        case 'price-asc':
          return getNumber(a, 'median', Number.POSITIVE_INFINITY) - getNumber(b, 'median', Number.POSITIVE_INFINITY);
        case 'price-desc':
          return getNumber(b, 'median', Number.NEGATIVE_INFINITY) - getNumber(a, 'median', Number.NEGATIVE_INFINITY);
        case 'count-desc':
          return getNumber(b, 'count', 0) - getNumber(a, 'count', 0);
        case 'name-asc':
          return (a.dataset.nameKo || '').localeCompare(b.dataset.nameKo || '', 'ko');
        case 'updated-desc':
          return getNumber(b, 'priority', 0) - getNumber(a, 'priority', 0);
        default:
          return getNumber(a, 'featureOrder', 0) - getNumber(b, 'featureOrder', 0);
      }
    }

    function isFavoritesCategory() {
      return activeCategory === 'favorites';
    }

    // Favorites live in auth state, so the catalog asks auth whether a card belongs here.
    function matchesActiveCategory(card) {
      if (activeCategory === 'all') return true;
      if (!isFavoritesCategory()) return card.dataset.categoryId === activeCategory;

      const authApi = window.nikonValueAuth;
      if (!authApi || typeof authApi.isFavorite !== 'function') return false;
      return authApi.isFavorite(card.dataset.productId || '');
    }

    function syncUrl() {
      const next = new URLSearchParams();
      if (activeCategory && activeCategory !== 'all') next.set('category', activeCategory);
      if (searchTerm) next.set('q', searchTerm);
      if (sortMode && sortMode !== 'featured') next.set('sort', sortMode);
      if (currencyMode === 'krw') next.set('currency', currencyMode);
      const query = next.toString();
      const target = query ? `?${query}` : window.location.pathname;
      history.replaceState({}, '', target);
    }

    function updateContext(visibleCards) {
      const activeTab = tabs.find((tab) => tab.dataset.categoryId === activeCategory);
      const label = activeTab ? activeTab.textContent.trim() : '전체';
      if (contextLabel) contextLabel.textContent = label;
      if (visibleCount) visibleCount.textContent = visibleCards.length.toLocaleString();
      if (emptyState) emptyState.hidden = visibleCards.length !== 0;
    }

    function updateTabs() {
      tabs.forEach((tab) => {
        tab.classList.toggle('active', tab.dataset.categoryId === activeCategory);
      });
      const allTab = document.querySelector('.category-tab[data-category-id="all"]');
      if (allTab) allTab.classList.toggle('active', activeCategory === 'all');
    }

    function updateRareWatch() {
      if (!rareWatch) return;
      if (isFavoritesCategory()) {
        rareWatch.hidden = true;
        return;
      }

      const visibleRareCards = rareCards.filter((card) => {
        const inCategory = card.dataset.categoryId === activeCategory || activeCategory === 'all';
        const inSearch = !searchTerm || (card.dataset.search || '').includes(searchTerm);
        card.hidden = !(inCategory && inSearch);
        return !card.hidden;
      });

      rareWatch.hidden = visibleRareCards.length === 0;
      if (!rareWatchSummary) return;

      if (activeCategory === 'all') {
        rareWatchSummary.textContent = `현재 ${visibleRareCards.length.toLocaleString()}개 모델에서 희귀 매물이 감지되었습니다.`;
        return;
      }

      const activeTab = tabs.find((tab) => tab.dataset.categoryId === activeCategory);
      const label = activeTab ? activeTab.textContent.trim() : '현재 분류';
      rareWatchSummary.textContent = `${label}에서 ${visibleRareCards.length.toLocaleString()}개 희귀 매물이 감지되었습니다.`;
    }

    function updateDealRadar() {
      if (!dealRadar) return;
      // 딜은 전 카테고리에 걸친 서버 렌더링 섹션이라 전체 탭에서만 보여준다.
      dealRadar.hidden = !!searchTerm || activeCategory !== 'all';
    }

    function updateFilmAtlas() {
      if (!filmAtlas) return;
      filmAtlas.hidden = activeCategory !== 'film-cameras';
    }

    function updateLensAtlas() {
      lensAtlases.forEach((el) => {
        el.hidden = el.dataset.categoryId !== activeCategory;
      });
    }

    function updateHeroImage() {
      const showLens = lensCategories.has(activeCategory);
      if (heroPictureDefault) heroPictureDefault.hidden = showLens;
      if (heroImageLens) heroImageLens.hidden = !showLens;
      if (heroHotspots) heroHotspots.hidden = showLens;
    }

    function applyCurrencyState() {
      applyMoneyElements(document, currencyMode, exchangeData);
      syncCurrencyButtons(currencyButtons, currencyMode, exchangeData);
      updateExchangeNotes(document, exchangeData);
      saveCurrency(currencyMode);
    }

    function applyState() {
      searchTerm = (searchInput?.value || '').trim().toLowerCase();
      sortMode = sortSelect?.value || 'featured';

      const visibleCards = cards.filter((card) => {
        const inCategory = matchesActiveCategory(card);
        const inSearch = !searchTerm || (card.dataset.search || '').includes(searchTerm);
        card.hidden = !(inCategory && inSearch);
        return !card.hidden;
      });

      visibleCards.sort(compareCards);
      const orderedCards = document.createDocumentFragment();
      visibleCards.forEach((card) => orderedCards.appendChild(card));
      grid.appendChild(orderedCards);

      updateTabs();
      updateContext(visibleCards);
      updateRareWatch();
      updateDealRadar();
      updateFilmAtlas();
      updateLensAtlas();
      updateHeroImage();
      applyCurrencyState();
      if (window.nikonValueAuth && typeof window.nikonValueAuth.onCategoryChange === 'function') {
        window.nikonValueAuth.onCategoryChange(activeCategory);
      }
      syncUrl();
    }

    window.nikonValueCatalog = {
      refresh() {
        applyState();
      },
    };

    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        activeCategory = tab.dataset.categoryId || 'all';
        applyState();
      });
    });

    currencyButtons.forEach((button) => {
      button.addEventListener('click', () => {
        currencyMode = normalizeCurrency(button.dataset.currency || 'usd', exchangeData);
        applyState();
      });
    });

    searchInput?.addEventListener('input', applyState);
    sortSelect?.addEventListener('change', applyState);

    var categoryNav = document.querySelector('.category-nav');
    var categoryContainer = document.querySelector('.category-nav__container');
    if (categoryNav && categoryContainer) {
      function checkScrollEnd() {
        var atEnd = categoryNav.scrollLeft + categoryNav.clientWidth >= categoryNav.scrollWidth - 4;
        var hasOverflow = categoryNav.scrollWidth > categoryNav.clientWidth;
        categoryContainer.classList.toggle('is-scrolled-end', atEnd || !hasOverflow);
      }
      categoryNav.addEventListener('scroll', checkScrollEnd, { passive: true });
      window.addEventListener('resize', checkScrollEnd);
      checkScrollEnd();
    }

    applyState();
  }

  function initProductPage() {
    // 인라인 history-data는 최근 10건짜리 부트스트랩 데이터다 (제품 페이지 크기 축소).
    // 전체 히스토리는 body[data-history-url]에서 가져오고, 실패하면 인라인 데이터로 폴백한다.
    let historyData = readJsonScript('history-data', []);
    const exchangeData = readJsonScript('exchange-rate-data', {});
    const buttons = Array.from(document.querySelectorAll('.period-btn'));
    const currencyButtons = Array.from(document.querySelectorAll('.currency-toggle__button[data-currency]'));
    const maToggle = document.querySelector('.ma-toggle');
    const emptyEl = document.getElementById('chart-empty');
    const canvas = document.getElementById('price-chart');
    const params = new URLSearchParams(window.location.search);
    let activePeriod = Number(document.body.dataset.defaultPeriod || '180');
    let activeCurrency = getInitialCurrency(params, exchangeData);
    let chartInstance = null;
    let maEnabled = false;

    if (!canvas || !emptyEl) return;

    function loadFullHistory() {
      const url = document.body.dataset.historyUrl;
      if (!url || typeof fetch !== 'function') return;
      fetch(url, { cache: 'no-cache' })
        .then((res) => (res.ok ? res.json() : Promise.reject(new Error('HTTP ' + res.status))))
        .then((data) => {
          // 인라인보다 짧거나 배열이 아니면 무시 — 이미 그려진 차트를 유지한다.
          if (!Array.isArray(data) || data.length <= historyData.length) return;
          historyData = data;
          renderChart(filterByPeriod(historyData, activePeriod));
        })
        .catch(() => {
          // 오프라인·404·파싱 오류: 인라인 10건으로 폴백한다.
          // (폴백 계약은 nikon_value/sitegen/pages.py 주석 참고)
        });
    }

    function syncUrl() {
      const next = new URLSearchParams(window.location.search);
      if (activeCurrency === 'krw') next.set('currency', activeCurrency);
      else next.delete('currency');
      const query = next.toString();
      const target = query ? `${window.location.pathname}?${query}` : window.location.pathname;
      history.replaceState({}, '', target);
    }

    function setEmpty(message) {
      emptyEl.hidden = false;
      emptyEl.textContent = message;
      if (chartInstance) {
        chartInstance.destroy();
        chartInstance = null;
      }
    }

    function renderChart(data) {
      if (typeof window.Chart === 'undefined') {
        setEmpty('차트 라이브러리를 불러오지 못했습니다.');
        return;
      }

      if (data.length < 2) {
        setEmpty('표시할 시계열 데이터가 충분하지 않습니다.');
        return;
      }

      emptyEl.hidden = true;
      const ctx = canvas.getContext('2d');
      const labels = data.map((entry) => entry.date);
      const medians = data.map((entry) => entry.median);
      const q1s = data.map((entry) => entry.q1);
      const q3s = data.map((entry) => entry.q3);

      if (chartInstance) chartInstance.destroy();

      const datasets = [
        {
          label: 'Q3',
          data: q3s,
          borderColor: 'transparent',
          backgroundColor: 'rgba(29, 29, 31, 0.08)',
          fill: '+1',
          pointRadius: 0,
          tension: 0.28,
        },
        {
          label: '중앙값',
          data: medians,
          borderColor: '#1d1d1f',
          backgroundColor: 'rgba(29, 29, 31, 0.12)',
          borderWidth: 2,
          pointRadius: data.length < 60 ? 3 : 0,
          pointHoverRadius: 5,
          tension: 0.28,
        },
        {
          label: 'Q1',
          data: q1s,
          borderColor: 'transparent',
          backgroundColor: 'rgba(29, 29, 31, 0.08)',
          fill: '-1',
          pointRadius: 0,
          tension: 0.28,
        },
      ];
      if (maEnabled) {
        // 툴팁 필터가 datasetIndex 1(중앙값)만 보므로 항상 뒤에 덧붙인다.
        datasets.push({
          label: '7일 이동평균',
          data: movingAverage(data, 7),
          borderColor: '#c08400',
          borderDash: [6, 4],
          borderWidth: 2,
          backgroundColor: 'transparent',
          fill: false,
          pointRadius: 0,
          tension: 0.28,
        });
      }

      chartInstance = new window.Chart(ctx, {
        type: 'line',
        data: {
          labels,
          datasets,
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: {
            mode: 'index',
            intersect: false,
          },
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                title(items) {
                  return items[0].label;
                },
                label(item) {
                  if (item.datasetIndex === 1) {
                    return `중앙값: ${formatMoney(item.parsed.y, {
                      currency: activeCurrency,
                      exchangeData,
                    })}`;
                  }
                  return null;
                },
                afterBody(items) {
                  const idx = items[0].dataIndex;
                  return `Q1-Q3: ${formatMoney(q1s[idx], {
                    currency: activeCurrency,
                    exchangeData,
                  })} - ${formatMoney(q3s[idx], {
                    currency: activeCurrency,
                    exchangeData,
                  })}`;
                },
              },
              filter(item) {
                return item.datasetIndex === 1;
              },
            },
          },
          scales: {
            x: {
              grid: { display: false },
              ticks: { maxTicksLimit: 8, font: { size: 11 } },
            },
            y: {
              grid: { color: 'rgba(0, 0, 0, 0.06)' },
              ticks: {
                callback(value) {
                  return formatMoney(Number(value), {
                    currency: activeCurrency,
                    exchangeData,
                  });
                },
                font: { size: 11 },
              },
            },
          },
        },
      });
    }

    function applyCurrencyState() {
      applyMoneyElements(document, activeCurrency, exchangeData);
      syncCurrencyButtons(currencyButtons, activeCurrency, exchangeData);
      updateExchangeNotes(document, exchangeData);
      saveCurrency(activeCurrency);
      syncUrl();
      renderChart(filterByPeriod(historyData, activePeriod));
    }

    function applyPeriod(period) {
      activePeriod = Number(period);
      buttons.forEach((button) => {
        button.classList.toggle('active', Number(button.dataset.period) === activePeriod);
      });
      renderChart(filterByPeriod(historyData, activePeriod));
    }

    if (maToggle) {
      maToggle.addEventListener('click', () => {
        maEnabled = !maEnabled;
        maToggle.classList.toggle('active', maEnabled);
        maToggle.setAttribute('aria-pressed', String(maEnabled));
        renderChart(filterByPeriod(historyData, activePeriod));
      });
    }

    buttons.forEach((button) => {
      button.addEventListener('click', () => applyPeriod(button.dataset.period));
    });

    currencyButtons.forEach((button) => {
      button.addEventListener('click', () => {
        activeCurrency = normalizeCurrency(button.dataset.currency || 'usd', exchangeData);
        applyCurrencyState();
      });
    });

    applyCurrencyState();
    applyPeriod(activePeriod);
    loadFullHistory();
  }

  initHeroEasterEgg();

  const pageType = document.body.dataset.page;
  if (pageType === 'catalog') {
    initCatalogPage();
  } else if (pageType === 'product') {
    initProductPage();
  }
})();
