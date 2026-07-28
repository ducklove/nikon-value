(function () {
  'use strict';

  // 모델 비교 페이지(compare.html) 전용 스크립트.
  //
  // 왜 site.js가 아니라 별도 파일인가
  // ---------------------------------------------------------------------------
  // 이 코드는 331장의 제품 페이지와 홈에는 필요 없다. site.js에 넣으면 모든
  // 페이지가 비교 뷰 코드를 내려받는다. 대신 site.js가 이미 쓰는 전역 결합 패턴
  // (window.nikonValueShared / nikonValueCurrency / nikonValueChartLoader)으로
  // 구현을 공유해 중복은 만들지 않는다. 로드 순서는 pages.py가 소유한다:
  //   js/site.js → js/auth.js → js/compare.js (모두 defer, 문서 순서대로 실행)
  //
  // Chart.js 로드 방식
  // ---------------------------------------------------------------------------
  // 제품 페이지처럼 <script defer>로 정적 로드하지 않고 auth.js의 온디맨드
  // 패턴을 쓴다. 비교 페이지는 ?ids= 없이 열리는 경우(선택기만 보이는 상태)가
  // 정상 진입 경로 중 하나이고, 그때는 그릴 차트가 아예 없다. 히스토리 fetch가
  // 끝나고 실제로 그릴 시리즈가 생겼을 때만 CDN을 때린다.

  var shared = window.nikonValueShared;
  var currencyApi = window.nikonValueCurrency;
  var chartLoader = window.nikonValueChartLoader;
  var escapeHtml = shared.escapeHtml;
  var formatMoney = shared.formatMoney;
  var filterByPeriod = shared.filterByPeriod;
  var parseCompareIds = shared.parseCompareIds;
  var buildCompareSeries = shared.buildCompareSeries;

  if (document.body.dataset.page !== 'compare') return;

  // 색맹 접근성을 고려한 구분 가능한 색 5개(최대 제품 수와 같다).
  var SERIES_COLORS = ['#1d1d1f', '#0071e3', '#c08400', '#0a7d5a', '#b03060'];
  var VALID_PERIODS = [30, 90, 180, 365, 0];
  var VALID_METRICS = ['median', 'count'];
  var LIQUIDITY_WINDOW_DAYS = 90;

  var products = readJsonScript('compare-products', []);
  var exchangeData = readJsonScript('exchange-rate-data', {});
  var productById = {};
  products.forEach(function (p) { if (p && p.id) productById[p.id] = p; });
  var knownIds = new Set(Object.keys(productById));

  var maxProducts = Number(document.body.dataset.compareMax || '5');
  if (!Number.isFinite(maxProducts) || maxProducts < 2) maxProducts = 5;

  var selectedIds = [];
  var historyCache = {};
  var chartInstance = null;
  var activePeriod = 180;
  var activeMetric = 'median';
  var activeCurrency = 'usd';
  var indexed = false;
  var pendingMessages = [];

  var searchInput = document.getElementById('compare-search-input');
  var suggestionList = document.getElementById('compare-suggestions');
  var chipList = document.getElementById('compare-chips');
  var statusEl = document.getElementById('compare-status');
  var canvas = document.getElementById('compare-chart');
  var emptyEl = document.getElementById('compare-chart-empty');
  var legendEl = document.getElementById('compare-legend');
  var tableBody = document.getElementById('compare-table-body');
  var periodButtons = Array.prototype.slice.call(document.querySelectorAll('#compare-periods .period-btn'));
  var metricButtons = Array.prototype.slice.call(document.querySelectorAll('.compare-modes [data-metric]'));
  var indexedToggle = document.getElementById('compare-indexed');
  var currencyButtons = Array.prototype.slice.call(document.querySelectorAll('.currency-toggle__button[data-currency]'));

  function readJsonScript(id, fallback) {
    var node = document.getElementById(id);
    if (!node || !node.textContent) return fallback;
    try {
      return JSON.parse(node.textContent);
    } catch (err) {
      console.error('Failed to parse JSON payload:', err);
      return fallback;
    }
  }

  // --- URL 파라미터 해석 -----------------------------------------------------
  // ids 외의 파라미터도 전부 허용 목록으로 검증한다. 값이 이상하면 조용히
  // 기본값으로 되돌린다 — 잘못된 URL 하나로 페이지가 비지 않게.
  function readParams() {
    var params = new URLSearchParams(window.location.search);

    var parsed = parseCompareIds(params.get('ids'), knownIds, maxProducts);
    selectedIds = parsed.ids;
    if (parsed.unknown.length) {
      pendingMessages.push('알 수 없는 모델 ID ' + parsed.unknown.length + '개를 건너뛰었습니다: ' + parsed.unknown.join(', '));
    }
    if (parsed.overflow || parsed.truncated) {
      pendingMessages.push('한 번에 비교할 수 있는 모델은 최대 ' + maxProducts + '개입니다.');
    }

    // 파라미터가 없으면 params.get()은 null이고 Number(null)은 0이다.
    // 0은 "전체 기간"이라는 유효한 값이라, 빈 값을 먼저 걸러내지 않으면
    // 파라미터 없는 URL이 기본 6개월이 아니라 전체 기간으로 열린다.
    var rawPeriod = params.get('period');
    if (rawPeriod !== null && rawPeriod !== '') {
      var period = Number(rawPeriod);
      if (VALID_PERIODS.indexOf(period) !== -1) activePeriod = period;
    }

    var metric = params.get('metric');
    if (VALID_METRICS.indexOf(metric) !== -1) activeMetric = metric;

    indexed = params.get('indexed') === '1';
    activeCurrency = currencyApi.getInitial(params, exchangeData);
  }

  function syncUrl() {
    var next = new URLSearchParams();
    if (selectedIds.length) next.set('ids', selectedIds.join(','));
    if (activePeriod !== 180) next.set('period', String(activePeriod));
    if (activeMetric !== 'median') next.set('metric', activeMetric);
    if (indexed) next.set('indexed', '1');
    if (activeCurrency === 'krw') next.set('currency', activeCurrency);
    // URLSearchParams는 쉼표를 %2C로 인코딩한다. 쿼리 값에서 쉼표는 원래 안전한
    // 문자이고, 공유되는 URL이라 읽기 쉬운 편이 낫다.
    var query = next.toString().replace(/%2C/g, ',');
    history.replaceState({}, '', query ? '?' + query : window.location.pathname);
  }

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
  }

  function productLabel(id) {
    var p = productById[id];
    return p ? p.name_ko : id;
  }

  // --- 선택 모델 칩 ----------------------------------------------------------
  function renderChips() {
    if (!chipList) return;
    chipList.innerHTML = '';
    selectedIds.forEach(function (id, index) {
      var li = document.createElement('li');
      li.className = 'compare-chip';
      li.style.setProperty('--chip-color', SERIES_COLORS[index % SERIES_COLORS.length]);
      li.innerHTML =
        '<span class="compare-chip__swatch" aria-hidden="true"></span>' +
        '<a class="compare-chip__name" href="products/' + encodeURIComponent(id) + '.html">' + escapeHtml(productLabel(id)) + '</a>' +
        '<button class="compare-chip__remove" type="button" data-remove-id="' + escapeHtml(id) + '"' +
        ' aria-label="' + escapeHtml(productLabel(id)) + ' 비교에서 제거">&times;</button>';
      chipList.appendChild(li);
    });
  }

  function addProduct(id) {
    if (!knownIds.has(id)) return;
    if (selectedIds.indexOf(id) !== -1) {
      setStatus(productLabel(id) + '는 이미 비교 목록에 있습니다.');
      return;
    }
    if (selectedIds.length >= maxProducts) {
      setStatus('한 번에 비교할 수 있는 모델은 최대 ' + maxProducts + '개입니다. 먼저 하나를 제거하세요.');
      return;
    }
    selectedIds.push(id);
    refresh();
  }

  function removeProduct(id) {
    var index = selectedIds.indexOf(id);
    if (index === -1) return;
    selectedIds.splice(index, 1);
    refresh();
  }

  // --- 검색 자동완성 ---------------------------------------------------------
  function renderSuggestions(term) {
    if (!suggestionList) return;
    var query = term.trim().toLowerCase();
    suggestionList.innerHTML = '';
    if (!query) {
      suggestionList.hidden = true;
      if (searchInput) searchInput.setAttribute('aria-expanded', 'false');
      return;
    }

    var matches = products.filter(function (p) {
      if (selectedIds.indexOf(p.id) !== -1) return false;
      return (p.id + ' ' + p.name_ko + ' ' + p.name_en + ' ' + p.category_label).toLowerCase().indexOf(query) !== -1;
    }).slice(0, 8);

    if (!matches.length) {
      suggestionList.hidden = true;
      if (searchInput) searchInput.setAttribute('aria-expanded', 'false');
      return;
    }

    matches.forEach(function (p) {
      var li = document.createElement('li');
      li.className = 'compare-suggestion';
      li.setAttribute('role', 'option');
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'compare-suggestion__button';
      button.setAttribute('data-add-id', p.id);
      button.innerHTML =
        '<strong>' + escapeHtml(p.name_ko) + '</strong>' +
        '<span>' + escapeHtml(p.name_en) + '</span>' +
        '<span class="compare-suggestion__meta">' + escapeHtml(p.category_label) + ' · 매물 ' + (p.count || 0) + '개</span>';
      li.appendChild(button);
      suggestionList.appendChild(li);
    });
    suggestionList.hidden = false;
    if (searchInput) searchInput.setAttribute('aria-expanded', 'true');
  }

  function clearSearch() {
    if (searchInput) {
      searchInput.value = '';
      searchInput.setAttribute('aria-expanded', 'false');
    }
    if (suggestionList) {
      suggestionList.innerHTML = '';
      suggestionList.hidden = true;
    }
  }

  // --- 히스토리 로딩 ---------------------------------------------------------
  function fetchHistory(id) {
    if (historyCache[id]) return Promise.resolve(historyCache[id]);
    return fetch('data/products/' + encodeURIComponent(id) + '.json', { cache: 'no-cache' })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        var history = Array.isArray(data) ? data : [];
        historyCache[id] = history;
        return history;
      })
      .catch(function () {
        historyCache[id] = [];
        return [];
      });
  }

  // --- 차트 -----------------------------------------------------------------
  function setEmpty(message) {
    if (emptyEl) {
      emptyEl.hidden = false;
      emptyEl.textContent = message;
    }
    if (chartInstance) {
      chartInstance.destroy();
      chartInstance = null;
    }
    if (legendEl) legendEl.innerHTML = '';
  }

  function formatValue(value) {
    if (value == null) return '-';
    if (indexed) return Number(value).toFixed(1);
    if (activeMetric === 'count') return Number(value).toLocaleString('ko-KR') + '개';
    return formatMoney(value, { currency: activeCurrency, exchangeData: exchangeData });
  }

  function renderLegend(series) {
    if (!legendEl) return;
    legendEl.innerHTML = '';
    series.forEach(function (item, index) {
      var span = document.createElement('span');
      span.className = 'compare-legend__item';
      span.style.setProperty('--chip-color', SERIES_COLORS[index % SERIES_COLORS.length]);
      span.innerHTML =
        '<span class="compare-legend__swatch" aria-hidden="true"></span>' +
        escapeHtml(item.label) + ' <b>' + escapeHtml(formatValue(item.last)) + '</b>';
      legendEl.appendChild(span);
    });
  }

  function renderChart(normalized) {
    if (!canvas) return;
    if (!normalized.series.length || normalized.labels.length < 2) {
      setEmpty(selectedIds.length ? '선택한 모델의 시계열 데이터가 충분하지 않습니다.' : '비교할 모델을 2개 이상 추가하세요.');
      return;
    }

    if (emptyEl) emptyEl.hidden = true;
    if (chartInstance) chartInstance.destroy();

    var datasets = normalized.series.map(function (item, index) {
      var color = SERIES_COLORS[index % SERIES_COLORS.length];
      return {
        label: item.label,
        data: item.data,
        borderColor: color,
        backgroundColor: color,
        borderWidth: 2,
        pointRadius: normalized.labels.length < 60 ? 2 : 0,
        pointHoverRadius: 5,
        tension: 0.28,
        fill: false,
        // 관측 공백(수집 실패·상장 없음)이 있어도 선을 이어 형태를 비교할 수 있게 한다.
        spanGaps: true,
      };
    });

    chartInstance = new window.Chart(canvas.getContext('2d'), {
      type: 'line',
      data: { labels: normalized.labels, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (item) {
                if (item.parsed.y == null) return null;
                return item.dataset.label + ': ' + formatValue(item.parsed.y);
              },
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { maxTicksLimit: 8, font: { size: 11 } } },
          y: {
            grid: { color: 'rgba(0, 0, 0, 0.06)' },
            ticks: {
              callback: function (value) { return formatValue(Number(value)); },
              font: { size: 11 },
            },
          },
        },
      },
    });

    renderLegend(normalized.series);
  }

  // --- 비교 표 ---------------------------------------------------------------
  function averageCount(history) {
    var recent = filterByPeriod(history, LIQUIDITY_WINDOW_DAYS);
    if (!recent.length) return null;
    var sum = recent.reduce(function (acc, e) { return acc + (e.count || 0); }, 0);
    return Math.round((sum / recent.length) * 10) / 10;
  }

  function periodChange(history) {
    var scoped = filterByPeriod(history, activePeriod).filter(function (e) { return e.median != null; });
    if (scoped.length < 2) return null;
    var first = scoped[0].median;
    var last = scoped[scoped.length - 1].median;
    if (!first) return null;
    return ((last - first) / first) * 100;
  }

  function renderTable(histories) {
    if (!tableBody) return;
    tableBody.innerHTML = '';
    selectedIds.forEach(function (id) {
      var product = productById[id] || {};
      var history = histories[id] || [];
      var avg = averageCount(history);
      var change = periodChange(history);
      var changeText = change == null ? '-' : (change > 0 ? '+' : '') + change.toFixed(1) + '%';
      var changeClass = change == null ? '' : (change > 0 ? ' class="compare-table__up"' : (change < 0 ? ' class="compare-table__down"' : ''));
      var row = document.createElement('tr');
      row.innerHTML =
        '<td><a href="products/' + encodeURIComponent(id) + '.html">' + escapeHtml(product.name_ko || id) + '</a></td>' +
        '<td>' + escapeHtml(formatMoney(product.median, { currency: activeCurrency, exchangeData: exchangeData })) + '</td>' +
        '<td>' + (product.count || 0) + '개</td>' +
        '<td>' + (avg == null ? '-' : avg + '개') + '</td>' +
        '<td' + changeClass + '>' + escapeHtml(changeText) + '</td>';
      tableBody.appendChild(row);
    });
  }

  // --- 렌더 파이프라인 -------------------------------------------------------
  function refresh() {
    renderChips();
    syncUrl();
    currencyApi.syncButtons(currencyButtons, activeCurrency, exchangeData);
    currencyApi.save(activeCurrency);
    periodButtons.forEach(function (b) {
      b.classList.toggle('active', Number(b.dataset.period) === activePeriod);
    });
    metricButtons.forEach(function (b) {
      b.classList.toggle('active', b.dataset.metric === activeMetric);
    });
    if (indexedToggle) {
      indexedToggle.classList.toggle('active', indexed);
      indexedToggle.setAttribute('aria-pressed', String(indexed));
    }

    if (!selectedIds.length) {
      setEmpty('비교할 모델을 2개 이상 추가하세요.');
      if (tableBody) tableBody.innerHTML = '';
      setStatus(pendingMessages.length ? pendingMessages.join(' ') : '위 검색창에서 모델을 골라 최대 ' + maxProducts + '개까지 겹쳐 볼 수 있습니다.');
      pendingMessages = [];
      return;
    }

    var ids = selectedIds.slice();
    Promise.all(ids.map(fetchHistory)).then(function (loaded) {
      // 로딩 중에 선택이 바뀌었으면 이 응답은 버린다.
      if (ids.join(',') !== selectedIds.join(',')) return;

      var histories = {};
      ids.forEach(function (id, i) { histories[id] = loaded[i] || []; });

      var items = ids.map(function (id) {
        return {
          id: id,
          label: productLabel(id),
          history: filterByPeriod(histories[id], activePeriod),
        };
      });
      var normalized = buildCompareSeries(items, {
        metric: activeMetric,
        mode: indexed ? 'indexed' : 'absolute',
      });

      renderTable(histories);

      // 히스토리가 비었거나(수집 전) 이 지표로 그릴 값이 하나도 없는(중앙값이
      // 전부 null인 매물 0건 모델) 경우 차트에는 선이 생기지 않는다. 표에는
      // 그대로 남으므로 왜 선이 없는지 알려 준다.
      var charted = {};
      normalized.series.forEach(function (s) { charted[s.id] = true; });
      var missing = ids.filter(function (id) { return !charted[id]; });
      var messages = pendingMessages.slice();
      pendingMessages = [];
      if (missing.length) {
        messages.push(
          '시세 기록이 없어 차트에서 빠진 모델 ' + missing.length + '개: ' +
          missing.map(productLabel).join(', ') + '.'
        );
      }
      if (selectedIds.length === 1) {
        messages.push('모델을 하나 더 추가하면 겹쳐 비교할 수 있습니다.');
      }
      setStatus(messages.join(' ') || selectedIds.length + '개 모델을 비교하고 있습니다.');

      if (!normalized.series.length) {
        setEmpty('선택한 모델의 시계열 데이터가 충분하지 않습니다.');
        return;
      }

      chartLoader.ensure().then(function (ok) {
        if (!ok) {
          setEmpty('차트 라이브러리를 불러오지 못했습니다.');
          return;
        }
        if (ids.join(',') !== selectedIds.join(',')) return;
        renderChart(normalized);
      });
    });
  }

  // --- 이벤트 ---------------------------------------------------------------
  if (searchInput) {
    searchInput.addEventListener('input', function () { renderSuggestions(searchInput.value); });
    searchInput.addEventListener('keydown', function (event) {
      if (event.key !== 'Escape') return;
      clearSearch();
    });
  }

  if (suggestionList) {
    suggestionList.addEventListener('click', function (event) {
      var button = event.target.closest('[data-add-id]');
      if (!button) return;
      addProduct(button.getAttribute('data-add-id'));
      clearSearch();
      if (searchInput) searchInput.focus();
    });
  }

  if (chipList) {
    chipList.addEventListener('click', function (event) {
      var button = event.target.closest('[data-remove-id]');
      if (!button) return;
      removeProduct(button.getAttribute('data-remove-id'));
    });
  }

  periodButtons.forEach(function (button) {
    button.addEventListener('click', function () {
      var value = Number(button.dataset.period);
      if (VALID_PERIODS.indexOf(value) === -1) return;
      activePeriod = value;
      refresh();
    });
  });

  metricButtons.forEach(function (button) {
    button.addEventListener('click', function () {
      var value = button.dataset.metric;
      if (VALID_METRICS.indexOf(value) === -1) return;
      activeMetric = value;
      refresh();
    });
  });

  if (indexedToggle) {
    indexedToggle.addEventListener('click', function () {
      indexed = !indexed;
      refresh();
    });
  }

  currencyButtons.forEach(function (button) {
    button.addEventListener('click', function () {
      activeCurrency = shared.normalizeCurrency(button.dataset.currency || 'usd', exchangeData);
      refresh();
    });
  });

  document.addEventListener('click', function (event) {
    if (!suggestionList || suggestionList.hidden) return;
    var target = event.target;
    if (target && typeof target.closest === 'function' && target.closest('.compare-search')) return;
    clearSearch();
  });

  readParams();
  currencyApi.updateNotes(document, exchangeData);
  refresh();
})();
