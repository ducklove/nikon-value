(function () {
  'use strict';

  const CURRENCY_STORAGE_KEY = 'nikon-value-currency';

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

  function getExchangeRate(exchangeData) {
    const raw = Number(exchangeData?.rate);
    return Number.isFinite(raw) && raw > 0 ? raw : null;
  }

  function normalizeCurrency(value, exchangeData) {
    if (value === 'krw' && getExchangeRate(exchangeData)) return 'krw';
    return 'usd';
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

  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
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

    var rangeHtml = '';
    if (d.q1 != null && d.q3 != null) {
      rangeHtml = '<div class="product-card__range">Q1-Q3: <span class="money-range">' +
        '<span class="money-value" data-money-usd="' + d.q1 + '" data-money-sign="auto">' + formatMoney(d.q1) + '</span>' +
        ' - ' +
        '<span class="money-value" data-money-usd="' + d.q3 + '" data-money-sign="auto">' + formatMoney(d.q3) + '</span>' +
        '</span></div>';
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
        '<div class="product-card__meta"><span>현재 매물 ' + (d.count || 0) + '개</span></div>' +
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
      '<div class="rare-watch-card__hint">최근 희귀 시세 ' + escapeHtml(d.rarity_price_hint || '공개 표본 부족') + '</div>' +
      '<p class="rare-watch-card__note">' + escapeHtml(d.rarity_note || '개별 상태 확인 필요') + '</p>';
    return a;
  }

  function initCatalogPage() {
    var grid = document.getElementById('product-grid');
    var cardsData = readJsonScript('cards-data', []);
    var rareWatchGrid = document.getElementById('rare-watch-grid');
    var rareWatch = document.getElementById('rare-watch');
    var rareWatchSummary = document.getElementById('rare-watch-summary');

    // Render product cards from JSON data
    var cards = cardsData.map(function (d) {
      var el = createProductCard(d);
      grid.appendChild(el);
      return el;
    });

    // Render rare watch cards
    var rareCardsData = cardsData
      .filter(function (c) { return c.is_rare && c.count > 0; })
      .sort(function (a, b) {
        return (-(a.rarity_sort || 0) + (b.rarity_sort || 0)) ||
               (-(a.median || 0) + (b.median || 0)) ||
               ((a.count || 0) - (b.count || 0)) ||
               (a.name_ko || '').localeCompare(b.name_ko || '', 'ko');
      });
    var rareCards = rareCardsData.map(function (d) {
      var el = createRareWatchCard(d);
      if (rareWatchGrid) rareWatchGrid.appendChild(el);
      return el;
    });
    if (rareWatch && rareCards.length > 0) rareWatch.hidden = false;

    const tabs = Array.from(document.querySelectorAll('.category-tab[data-category-id]'));
    const searchInput = document.getElementById('search-input');
    const sortSelect = document.getElementById('sort-select');
    const visibleCount = document.getElementById('visible-count');
    const contextLabel = document.getElementById('catalog-context');
    const emptyState = document.getElementById('catalog-empty');
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

      const visibleRareCards = rareCards.filter((card) => {
        const inCategory = activeCategory === 'all' || card.dataset.categoryId === activeCategory;
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
        const inCategory = activeCategory === 'all' || card.dataset.categoryId === activeCategory;
        const inSearch = !searchTerm || (card.dataset.search || '').includes(searchTerm);
        card.hidden = !(inCategory && inSearch);
        return !card.hidden;
      });

      visibleCards.sort(compareCards);
      visibleCards.forEach((card) => grid.appendChild(card));

      updateTabs();
      updateContext(visibleCards);
      updateRareWatch();
      applyCurrencyState();
      syncUrl();
    }

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

    applyState();
  }

  function initProductPage() {
    const historyData = readJsonScript('history-data', []);
    const exchangeData = readJsonScript('exchange-rate-data', {});
    const buttons = Array.from(document.querySelectorAll('.period-btn'));
    const currencyButtons = Array.from(document.querySelectorAll('.currency-toggle__button[data-currency]'));
    const emptyEl = document.getElementById('chart-empty');
    const canvas = document.getElementById('price-chart');
    const params = new URLSearchParams(window.location.search);
    let activePeriod = Number(document.body.dataset.defaultPeriod || '180');
    let activeCurrency = getInitialCurrency(params, exchangeData);
    let chartInstance = null;

    if (!canvas || !emptyEl) return;

    function syncUrl() {
      const next = new URLSearchParams(window.location.search);
      if (activeCurrency === 'krw') next.set('currency', activeCurrency);
      else next.delete('currency');
      const query = next.toString();
      const target = query ? `${window.location.pathname}?${query}` : window.location.pathname;
      history.replaceState({}, '', target);
    }

    function getReferenceDate(data) {
      if (!data.length) return new Date();
      return new Date(data[data.length - 1].date + 'T00:00:00Z');
    }

    function filterByPeriod(data, days) {
      if (!days || data.length === 0) return data;
      const cutoff = getReferenceDate(data);
      cutoff.setUTCDate(cutoff.getUTCDate() - days);
      const cutoffStr = cutoff.toISOString().split('T')[0];
      return data.filter((entry) => entry.date >= cutoffStr);
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

      chartInstance = new window.Chart(ctx, {
        type: 'line',
        data: {
          labels,
          datasets: [
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
          ],
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
  }

  const pageType = document.body.dataset.page;
  if (pageType === 'catalog') {
    initCatalogPage();
  } else if (pageType === 'product') {
    initProductPage();
  }
})();
