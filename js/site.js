(function () {
  'use strict';

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

  function formatMoney(value) {
    return value === null || value === undefined || value === ''
      ? '-'
      : `$${Number(value).toLocaleString()}`;
  }

  function initCatalogPage() {
    const grid = document.getElementById('product-grid');
    const cards = Array.from(grid.querySelectorAll('.product-card[data-product-id]'));
    const tabs = Array.from(document.querySelectorAll('.category-tab[data-category-id]'));
    const searchInput = document.getElementById('search-input');
    const sortSelect = document.getElementById('sort-select');
    const visibleCount = document.getElementById('visible-count');
    const contextLabel = document.getElementById('catalog-context');
    const emptyState = document.getElementById('catalog-empty');
    const rareWatch = document.getElementById('rare-watch');
    const rareWatchSummary = document.getElementById('rare-watch-summary');
    const rareCards = Array.from(document.querySelectorAll('.rare-watch-card[data-category-id]'));
    const params = new URLSearchParams(window.location.search);

    let activeCategory = params.get('category') || 'all';
    let searchTerm = params.get('q') || '';
    let sortMode = params.get('sort') || 'featured';

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
      syncUrl();
    }

    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        activeCategory = tab.dataset.categoryId || 'all';
        applyState();
      });
    });

    searchInput?.addEventListener('input', applyState);
    sortSelect?.addEventListener('change', applyState);

    applyState();
  }

  function initProductPage() {
    const historyData = readJsonScript('history-data', []);
    const buttons = Array.from(document.querySelectorAll('.period-btn'));
    const emptyEl = document.getElementById('chart-empty');
    const canvas = document.getElementById('price-chart');
    let activePeriod = Number(document.body.dataset.defaultPeriod || '180');
    let chartInstance = null;

    if (!canvas || !emptyEl) return;

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
                    return `중앙값: ${formatMoney(item.parsed.y)}`;
                  }
                  return null;
                },
                afterBody(items) {
                  const idx = items[0].dataIndex;
                  return `Q1-Q3: ${formatMoney(q1s[idx])} - ${formatMoney(q3s[idx])}`;
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
                  return `$${Number(value).toLocaleString()}`;
                },
                font: { size: 11 },
              },
            },
          },
        },
      });
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

    applyPeriod(activePeriod);
  }

  const pageType = document.body.dataset.page;
  if (pageType === 'catalog') {
    initCatalogPage();
  } else if (pageType === 'product') {
    initProductPage();
  }
})();
