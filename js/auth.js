(function () {
  'use strict';

  // 빌드 시 페이지에 주입되는 meta 태그가 있으면 우선 사용한다 (환경별 API 주소).
  var apiBaseMeta = document.querySelector('meta[name="nikon-api-base"]');
  var API_BASE = (apiBaseMeta && apiBaseMeta.content) || 'https://cantabile.tplinkdns.com';
  var TOKEN_KEY = 'nikon-value-token';

  // 공용 순수 함수 모듈. js/site.js가 window.nikonValueShared로 공개한다.
  // 페이지는 site.js → auth.js 순서로 <script defer>를 로드하고(defer는 문서 순서대로
  // 실행된다) 그 순서는 nikon_value/sitegen/pages.py가 소유하므로, 여기서는 항상
  // 준비된 상태다. 과거 auth.js가 따로 갖고 있던 DOM 기반 escapeHtml은
  // 큰따옴표를 이스케이프하지 않아 속성값 삽입에 쓰면 위험했다 — 구현을 하나로 합쳤다.
  var shared = window.nikonValueShared;
  var escapeHtml = shared.escapeHtml;
  var formatUsd = shared.formatUsd;
  var buildSummedSeries = shared.buildSummedSeries;
  var buildSummedSeriesNote = shared.buildSummedSeriesNote;

  // --- Token management ---
  function getToken() { return localStorage.getItem(TOKEN_KEY); }
  function setToken(token) { localStorage.setItem(TOKEN_KEY, token); }
  function clearToken() { localStorage.removeItem(TOKEN_KEY); }

  function getReturnToPath() {
    var path = window.location.pathname.replace(/\/index\.html$/, '/');
    var homeLink = document.querySelector('.site-link[href$="index.html"]');
    if (!homeLink) return path;
    try {
      var siteRoot = new URL(homeLink.getAttribute('href'), window.location.href)
        .pathname
        .replace(/\/index\.html$/, '');
      if (siteRoot && path.indexOf(siteRoot + '/') === 0) {
        return path.slice(siteRoot.length) || '/';
      }
      if (siteRoot && path === siteRoot) return '/';
    } catch (e) {
      console.warn('Failed to resolve site root:', e.message);
    }
    return path;
  }

  function checkHashToken() {
    var hash = window.location.hash;
    var match = hash.match(/^#token=(.+)$/);
    if (match) {
      setToken(match[1]);
      history.replaceState(null, '', window.location.pathname + window.location.search);
    }
  }

  // --- API calls ---
  function apiFetch(path, options) {
    var token = getToken();
    var headers = Object.assign({}, options && options.headers ? options.headers : {});
    if (token) headers['Authorization'] = 'Bearer ' + token;
    // headers를 마지막에 병합해야 호출 측 headers가 Authorization을 덮어쓰지 않는다.
    return fetch(API_BASE + path, Object.assign({}, options || {}, { headers: headers }))
      .then(function (resp) {
        if (resp.status === 401) { clearToken(); renderLoggedOut(); return null; }
        return resp;
      })
      .catch(function (e) {
        console.warn('API server unreachable:', e.message);
        return null;
      });
  }

  function fetchMe() {
    return apiFetch('/api/me').then(function (r) { return r && r.ok ? r.json() : null; });
  }

  function fetchFavorites() {
    return apiFetch('/api/favorites').then(function (r) {
      if (!r || !r.ok) return [];
      return r.json().then(function (d) { return d.favorites || []; });
    });
  }

  function addFavorite(pid) { return apiFetch('/api/favorites/' + encodeURIComponent(pid), { method: 'PUT' }); }
  function removeFavorite(pid) { return apiFetch('/api/favorites/' + encodeURIComponent(pid), { method: 'DELETE' }); }

  function fetchAlerts() {
    return apiFetch('/api/alerts').then(function (r) {
      if (!r || !r.ok) return null; // null = 서버가 알림 기능을 아직 지원하지 않거나 오류
      return r.json().then(function (d) { return d.alerts || []; });
    });
  }

  function putAlert(pid, price) {
    return apiFetch('/api/alerts/' + encodeURIComponent(pid), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_price: price })
    });
  }

  function deleteAlert(pid) { return apiFetch('/api/alerts/' + encodeURIComponent(pid), { method: 'DELETE' }); }

  // 텔레그램 연동. 구버전 서버(엔드포인트 없음)에서는 null을 반환해 관련 UI를 숨긴다.
  function fetchTelegramStatus() {
    return apiFetch('/api/me/telegram').then(function (r) {
      if (!r || !r.ok) return null;
      return r.json().catch(function () { return null; });
    });
  }

  function requestTelegramLinkCode() {
    return apiFetch('/api/me/telegram/link-code', { method: 'PUT' }).then(function (r) {
      if (!r || !r.ok) return null;
      return r.json().catch(function () { return null; });
    });
  }

  function deleteTelegramLink() { return apiFetch('/api/me/telegram', { method: 'DELETE' }); }

  function refreshCatalog() {
    if (window.nikonValueCatalog && typeof window.nikonValueCatalog.refresh === 'function') {
      window.nikonValueCatalog.refresh();
    }
  }

  // --- State ---
  var currentUser = null;
  var favoriteSet = new Set();
  var cardsReady = false;

  window.nikonValueAuth = window.nikonValueAuth || {};
  window.nikonValueAuth.isFavorite = function (productId) {
    return favoriteSet.has(productId);
  };
  // site.js의 applyState가 탭 전환·검색·하트 토글 시마다 호출한다.
  // 표시 중이고 관심 항목 수가 그대로면 다시 그리지 않는다(재호출 안전).
  window.nikonValueAuth.onCategoryChange = function (category) {
    if (category !== 'favorites') {
      hideDashboard();
      return;
    }
    if (!currentUser) {
      hideDashboard();
      return;
    }
    var panel = document.getElementById('favorites-dashboard');
    var stale = !panel || panel.hidden
      || panel.getAttribute('data-fav-count') !== String(favoriteSet.size);
    if (stale) renderDashboard();
  };

  // --- UI: Auth area ---
  function renderLoggedIn(user) {
    var area = document.getElementById('auth-area');
    if (!area) return;
    area.innerHTML =
      '<span class="auth-user-name">' + escapeHtml(user.name || user.email || '사용자') + '</span>' +
      '<button class="auth-btn auth-btn--logout" id="logout-btn">로그아웃</button>';
    document.getElementById('logout-btn').addEventListener('click', handleLogout);
    // Show favorites tab
    var favTab = document.getElementById('favorites-tab');
    if (favTab) favTab.hidden = false;
    // Inject/show favorite buttons on cards
    if (cardsReady) {
      injectFavoriteButtons();
      showFavoriteButtons(true);
      updateAllFavoriteButtons();
    }
  }

  function renderLoggedOut() {
    currentUser = null;
    favoriteSet.clear();
    var area = document.getElementById('auth-area');
    if (!area) return;
    var returnTo = encodeURIComponent(getReturnToPath());
    area.innerHTML =
      '<div class="auth-login-dropdown">' +
        '<button class="auth-btn auth-btn--login" id="login-toggle">로그인</button>' +
        '<div class="auth-dropdown-menu" id="login-menu" hidden>' +
          '<a href="' + API_BASE + '/auth/google?return_to=' + returnTo + '" class="auth-dropdown-item auth-dropdown-item--google">Google 계정으로 로그인</a>' +
        '</div>' +
      '</div>';
    document.getElementById('login-toggle').addEventListener('click', function (e) {
      e.stopPropagation();
      var menu = document.getElementById('login-menu');
      menu.hidden = !menu.hidden;
    });
    // Hide favorites tab and switch to "all" if currently on favorites
    var favTab = document.getElementById('favorites-tab');
    if (favTab) {
      favTab.hidden = true;
      if (favTab.classList.contains('active')) {
        var allTab = document.querySelector('[data-category-id="all"]');
        if (allTab) allTab.click();
      }
    }
    // Hide favorite buttons
    updateAllFavoriteButtons();
    showFavoriteButtons(false);
    refreshCatalog();
    renderAlertPanelLoggedOut();
    hideDashboard();
  }

  // --- UI: Favorite buttons on cards ---
  function injectFavoriteButtons() {
    document.querySelectorAll('.product-card[data-product-id]').forEach(function (card) {
      if (card.querySelector('.favorite-btn')) return;
      var pid = card.getAttribute('data-product-id');
      var btn = document.createElement('button');
      btn.className = 'favorite-btn';
      btn.setAttribute('data-product-id', pid);
      btn.setAttribute('aria-label', '관심 목록에 추가');
      btn.textContent = '\u2661';
      btn.addEventListener('click', handleFavoriteClick);
      var body = card.querySelector('.product-card__body');
      if (body) body.prepend(btn);
    });
  }

  function showFavoriteButtons(show) {
    document.querySelectorAll('.favorite-btn').forEach(function (btn) {
      btn.style.display = show ? '' : 'none';
    });
  }

  function updateAllFavoriteButtons() {
    document.querySelectorAll('.favorite-btn').forEach(function (btn) {
      var pid = btn.getAttribute('data-product-id');
      var active = favoriteSet.has(pid);
      btn.classList.toggle('favorite--active', active);
      btn.textContent = active ? '\u2665' : '\u2661';
      btn.setAttribute('aria-label', active ? '관심 목록에서 제거' : '관심 목록에 추가');
    });
  }

  function handleFavoriteClick(e) {
    e.preventDefault();
    e.stopPropagation();
    var btn = e.currentTarget;
    var pid = btn.getAttribute('data-product-id');
    if (!currentUser) return;
    if (favoriteSet.has(pid)) {
      favoriteSet.delete(pid);
      removeFavorite(pid);
    } else {
      favoriteSet.add(pid);
      addFavorite(pid);
    }
    updateAllFavoriteButtons();
    refreshCatalog();
  }

  function handleLogout() {
    clearToken();
    renderLoggedOut();
  }

  // 참고: 과거의 setupFavoritesTab(자체 탭 필터링)은 site.js의 applyState가
  // activeCategory === 'favorites'를 처리하면서 호출되지 않는 데드 코드가 되어
  // 제거했다. 대시보드 토글은 nikonValueAuth.onCategoryChange 훅으로 연동한다.

  // --- UI: Price alert panel (제품 상세 페이지 전용) ---
  var alertMap = new Map();

  function getProductPageId() {
    if (document.body.getAttribute('data-page') !== 'product') return null;
    var match = window.location.pathname.match(/\/products\/([a-z0-9-]+)\.html$/);
    return match ? match[1] : null;
  }

  function getPagePrimaryMedian() {
    var el = document.querySelector('.price-card--primary .money-value[data-money-usd]');
    if (!el) return null;
    var value = parseFloat(el.getAttribute('data-money-usd'));
    return isNaN(value) ? null : value;
  }

  function ensureAlertPanel() {
    if (!getProductPageId()) return null;
    var panel = document.getElementById('price-alert-panel');
    if (panel) return panel;
    var summary = document.querySelector('.price-summary');
    if (!summary) return null;
    panel = document.createElement('section');
    panel.id = 'price-alert-panel';
    panel.className = 'price-alert';
    panel.setAttribute('aria-label', '가격 알림 설정');
    summary.insertAdjacentElement('afterend', panel);
    return panel;
  }

  function removeAlertPanel() {
    var panel = document.getElementById('price-alert-panel');
    if (panel) panel.remove();
  }

  function setAlertStatus(message, isError) {
    var status = document.getElementById('price-alert-status');
    if (!status) return;
    status.textContent = message || '';
    status.classList.toggle('price-alert__status--error', !!isError);
  }

  function buildAlertTitle() {
    var title = document.createElement('strong');
    title.className = 'price-alert__title';
    title.textContent = '가격 알림';
    return title;
  }

  function buildAlertNote(text) {
    var note = document.createElement('p');
    note.className = 'price-alert__note';
    note.textContent = text;
    return note;
  }

  function renderAlertPanelLoggedOut() {
    stopTelegramPoll();
    telegramStatus = null;
    var panel = ensureAlertPanel();
    if (!panel) return;
    panel.innerHTML = '';
    panel.appendChild(buildAlertTitle());
    panel.appendChild(buildAlertNote('로그인하면 제품별 목표가 알림을 설정할 수 있습니다. 알림은 텔레그램으로 발송됩니다.'));
  }

  function renderAlertPanelLoggedIn(pid) {
    stopTelegramPoll();
    var panel = ensureAlertPanel();
    if (!panel) return;
    panel.innerHTML = '';

    var existing = alertMap.get(pid) || null;

    var head = document.createElement('div');
    head.className = 'price-alert__head';
    head.appendChild(buildAlertTitle());
    var status = document.createElement('span');
    status.className = 'price-alert__status';
    status.id = 'price-alert-status';
    head.appendChild(status);

    var row = document.createElement('div');
    row.className = 'price-alert__row';
    var label = document.createElement('label');
    label.className = 'visually-hidden';
    label.setAttribute('for', 'price-alert-input');
    label.textContent = '목표가 (USD)';
    var input = document.createElement('input');
    input.type = 'number';
    input.id = 'price-alert-input';
    input.min = '1';
    input.step = '1';
    var median = getPagePrimaryMedian();
    if (existing) {
      input.value = String(existing.target_price);
    } else if (median) {
      input.placeholder = '예: ' + Math.round(median * 0.9);
    }
    var save = document.createElement('button');
    save.type = 'button';
    save.className = 'price-alert__btn';
    save.textContent = existing ? '목표가 변경' : '알림 설정';
    save.addEventListener('click', function () { handleAlertSave(pid); });
    row.appendChild(label);
    row.appendChild(input);
    row.appendChild(save);
    if (existing) {
      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'price-alert__btn price-alert__btn--ghost';
      remove.textContent = '해제';
      remove.addEventListener('click', function () { handleAlertRemove(pid); });
      row.appendChild(remove);
    }

    panel.appendChild(head);
    panel.appendChild(row);
    panel.appendChild(buildAlertNote(
      existing
        ? '중앙값이 목표가 USD ' + existing.target_price + ' 이하로 내려가면 알림을 보내드립니다.'
        : '중앙값이 목표가(USD) 이하로 내려가면 알림을 보내드립니다.'
    ));

    var channel = buildTelegramSection(pid);
    if (channel) panel.appendChild(channel);

    if (existing && existing.triggered) {
      setAlertStatus('목표가 도달 알림이 발송된 상태입니다. 가격이 회복되면 다시 활성화됩니다.');
    }
  }

  // --- UI: 텔레그램 알림 채널 ---
  var telegramStatus = null; // null = 서버가 텔레그램 API를 지원하지 않음(구버전)
  var telegramPollTimer = null;
  var telegramPollLeft = 0;

  function stopTelegramPoll() {
    if (telegramPollTimer) {
      clearInterval(telegramPollTimer);
      telegramPollTimer = null;
    }
  }

  function buildChannelBtn(text, ghost, onClick) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'price-alert__btn' + (ghost ? ' price-alert__btn--ghost' : '');
    btn.textContent = text;
    btn.addEventListener('click', onClick);
    return btn;
  }

  function buildChannelRow() {
    var row = document.createElement('div');
    row.className = 'price-alert__row';
    return row;
  }

  function buildTelegramSection(pid) {
    // 서버가 텔레그램 API를 지원하지 않으면(구버전) 채널 UI 자체를 노출하지 않는다.
    if (!telegramStatus) return null;

    var wrap = document.createElement('div');

    if (!telegramStatus.configured) {
      wrap.appendChild(buildAlertNote(
        '알림 채널: 텔레그램 봇이 아직 설정되지 않았습니다(관리자 준비 중). '
        + '설정한 목표가는 그대로 보관되며, 채널이 준비되면 대기 중인 알림이 발송됩니다.'
      ));
      return wrap;
    }

    var row = buildChannelRow();
    if (telegramStatus.linked) {
      wrap.appendChild(buildAlertNote('알림 채널: 텔레그램 연동됨. 목표가에 도달하면 텔레그램으로 알려드립니다.'));
      row.appendChild(buildChannelBtn('텔레그램 연동 해제', true, function () { handleTelegramUnlink(pid); }));
      wrap.appendChild(row);
      return wrap;
    }

    wrap.appendChild(buildAlertNote(
      '알림 채널: 텔레그램 미연동. 연동해야 알림을 받을 수 있습니다. '
      + '연동 전에 목표가에 도달한 알림은 사라지지 않고 대기하다가 연동 직후 발송됩니다.'
    ));
    row.appendChild(buildChannelBtn('텔레그램 연동하기', false, function () { handleTelegramLink(pid); }));
    wrap.appendChild(row);
    var box = document.createElement('div');
    box.id = 'telegram-link-box';
    wrap.appendChild(box);
    return wrap;
  }

  function refreshTelegramStatus(pid, onLinked) {
    return fetchTelegramStatus().then(function (status) {
      if (!status) return false;
      var wasLinked = telegramStatus && telegramStatus.linked;
      telegramStatus = status;
      if (status.linked && !wasLinked) {
        stopTelegramPoll();
        renderAlertPanelLoggedIn(pid);
        if (onLinked) onLinked();
        return true;
      }
      return false;
    });
  }

  function renderTelegramLinkCode(pid, data) {
    var box = document.getElementById('telegram-link-box');
    if (!box) return;
    box.innerHTML = '';

    var minutes = Math.max(1, Math.round((data.expires_in || 600) / 60));
    box.appendChild(buildAlertNote(
      '아래 일회용 코드를 텔레그램 봇에게 메시지로 보내주세요. ' + minutes + '분 안에 사용해야 하며 한 번만 쓸 수 있습니다.'
    ));

    var row = buildChannelRow();
    var code = document.createElement('code');
    code.textContent = data.code;
    code.style.fontSize = '1.05rem';
    code.style.fontWeight = '700';
    code.style.letterSpacing = '0.12em';
    row.appendChild(code);

    if (data.deep_link) {
      var link = document.createElement('a');
      link.className = 'price-alert__btn';
      link.href = data.deep_link;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = '텔레그램에서 봇 열기';
      row.appendChild(link);
    }

    row.appendChild(buildChannelBtn('연동 확인', true, function () {
      refreshTelegramStatus(pid).then(function (linked) {
        if (!linked) setAlertStatus('아직 연동이 확인되지 않았습니다. 봇에게 코드를 보낸 뒤 다시 확인해 주세요.');
      });
    }));
    box.appendChild(row);

    if (!data.deep_link && data.bot_username) {
      box.appendChild(buildAlertNote('봇 계정: @' + data.bot_username));
    }

    // 코드를 보내면 곧 연동되므로 2분간 5초 간격으로 상태를 확인한다.
    stopTelegramPoll();
    telegramPollLeft = 24;
    telegramPollTimer = setInterval(function () {
      telegramPollLeft -= 1;
      if (telegramPollLeft <= 0) { stopTelegramPoll(); return; }
      refreshTelegramStatus(pid, function () {
        setAlertStatus('텔레그램 연동이 완료되었습니다.');
      });
    }, 5000);
  }

  function handleTelegramLink(pid) {
    setAlertStatus('연동 코드를 발급하는 중...');
    requestTelegramLinkCode().then(function (data) {
      if (!data || !data.code) {
        setAlertStatus('연동 코드를 발급하지 못했습니다. 잠시 후 다시 시도해 주세요.', true);
        return;
      }
      setAlertStatus('');
      renderTelegramLinkCode(pid, data);
    });
  }

  function handleTelegramUnlink(pid) {
    deleteTelegramLink().then(function (r) {
      if (r && r.ok) {
        telegramStatus = Object.assign({}, telegramStatus, { linked: false, linked_at: null });
        renderAlertPanelLoggedIn(pid);
        setAlertStatus('텔레그램 연동을 해제했습니다.');
      } else {
        setAlertStatus('텔레그램 연동 해제에 실패했습니다.', true);
      }
    });
  }

  function handleAlertSave(pid) {
    var input = document.getElementById('price-alert-input');
    var price = parseFloat(input && input.value);
    if (!price || price <= 0) {
      setAlertStatus('올바른 목표가를 입력하세요.', true);
      return;
    }
    putAlert(pid, price).then(function (r) {
      if (r && r.ok) {
        alertMap.set(pid, { product_id: pid, target_price: price, triggered: false });
        renderAlertPanelLoggedIn(pid);
        setAlertStatus('알림이 설정되었습니다.');
      } else if (r) {
        r.json().then(function (d) {
          setAlertStatus((d && d.message) || '알림 설정에 실패했습니다.', true);
        }).catch(function () {
          setAlertStatus('알림 설정에 실패했습니다.', true);
        });
      } else {
        setAlertStatus('API 서버에 연결할 수 없습니다.', true);
      }
    });
  }

  function handleAlertRemove(pid) {
    deleteAlert(pid).then(function (r) {
      if (r && r.ok) {
        alertMap.delete(pid);
        renderAlertPanelLoggedIn(pid);
        setAlertStatus('알림이 해제되었습니다.');
      } else {
        setAlertStatus('알림 해제에 실패했습니다.', true);
      }
    });
  }

  function initAlertPanel() {
    var pid = getProductPageId();
    if (!pid) return;
    if (!currentUser) {
      renderAlertPanelLoggedOut();
      return;
    }
    fetchAlerts().then(function (alerts) {
      if (alerts === null) {
        // 서버가 아직 알림 API를 지원하지 않으면 패널을 노출하지 않는다.
        removeAlertPanel();
        return null;
      }
      alertMap = new Map(alerts.map(function (a) { return [a.product_id, a]; }));
      // 텔레그램 API가 없는 구버전 서버면 null이 되어 채널 UI만 조용히 빠진다.
      return fetchTelegramStatus().then(function (status) {
        telegramStatus = status;
        renderAlertPanelLoggedIn(pid);
      });
    });
  }

  // --- UI: Favorites value dashboard (관심 목록 탭 전용) ---
  //
  // 두 개의 축을 토글로 오간다.
  //   'indexed'  : buildSummedSeries가 계산한 연쇄 지수(첫날=100). 기본값.
  //                관심목록에 제품이 늦게 합류해도 값이 튀지 않는 축이다.
  //   'absolute' : 합산 절대액(USD). "내 컬렉션 가치"를 보고 싶을 때.
  //                합류 시점에 계단이 생기는 축이라 안내 문구가 그렇게 말한다.
  // 용어("지수화 (첫날=100)")와 토글 버튼 스타일은 비교 페이지(js/compare.js)와
  // 맞췄다 — 같은 개념을 두 화면에서 다르게 부르지 않기 위해서다.
  var dashboardChart = null;
  var dashboardView = 'indexed';
  var dashboardSeries = null;

  // 구성이 바뀐 날(합산에 들어간 제품 수가 직전 포인트와 다른 날)의 인덱스.
  function compositionChangeIndexes(series) {
    var result = [];
    for (var i = 1; i < series.length; i++) {
      if (series[i].known !== series[i - 1].known) result.push(i);
    }
    return result;
  }

  function getCardsData() {
    var node = document.getElementById('cards-data');
    if (!node) return [];
    try {
      return JSON.parse(node.textContent) || [];
    } catch (e) {
      return [];
    }
  }

  function ensureDashboard() {
    var grid = document.getElementById('product-grid');
    if (!grid) return null;
    var panel = document.getElementById('favorites-dashboard');
    if (panel) return panel;
    panel = document.createElement('section');
    panel.id = 'favorites-dashboard';
    panel.className = 'fav-dashboard';
    panel.setAttribute('aria-label', '관심 목록 가치 요약');
    panel.hidden = true;
    grid.insertAdjacentElement('beforebegin', panel);
    return panel;
  }

  function hideDashboard() {
    var panel = document.getElementById('favorites-dashboard');
    if (panel) panel.hidden = true;
  }

  function destroyDashboardChart() {
    if (!dashboardChart) return;
    dashboardChart.destroy();
    dashboardChart = null;
  }

  function buildDashStat(value, label) {
    var stat = document.createElement('div');
    stat.className = 'fav-dashboard__stat';
    var strong = document.createElement('b');
    strong.textContent = value;
    var span = document.createElement('span');
    span.textContent = label;
    stat.appendChild(strong);
    stat.appendChild(span);
    return stat;
  }

  // 보기 토글(지수 / 합산액). 데이터가 실제로 그려지기 전에는 숨겨 둔다.
  function buildViewToggle() {
    var modes = document.createElement('div');
    modes.className = 'fav-dashboard__modes';
    modes.id = 'fav-dashboard-modes';
    modes.setAttribute('role', 'group');
    modes.setAttribute('aria-label', '합산 추이 보기 방식');
    modes.hidden = true;

    [
      { view: 'indexed', label: '지수화 (첫날=100)', title: '관심목록 구성이 바뀌어도 튀지 않는 축입니다. 그날 함께 관측된 제품들의 변동률만 이어 붙여 계산합니다.' },
      { view: 'absolute', label: '합산액 (USD)', title: '중앙값을 그대로 더한 금액입니다. 제품이 합류하는 날 계단처럼 올라갑니다.' },
    ].forEach(function (mode) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'period-btn';
      button.setAttribute('data-dashboard-view', mode.view);
      button.title = mode.title;
      button.textContent = mode.label;
      button.addEventListener('click', function () {
        if (dashboardView === mode.view) return;
        dashboardView = mode.view;
        applyDashboardView();
      });
      modes.appendChild(button);
    });
    return modes;
  }

  function syncViewButtons() {
    document.querySelectorAll('[data-dashboard-view]').forEach(function (button) {
      var active = button.getAttribute('data-dashboard-view') === dashboardView;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }

  // 차트에서 실선/점선/주황 점이 각각 무슨 뜻인지 알려주는 키.
  // 점선·주황 점은 실제로 그런 구간이 있을 때만 붙인다(없으면 노이즈다).
  function renderDashboardLegend(series) {
    var legend = document.getElementById('fav-dashboard-legend');
    if (!legend) return;
    legend.innerHTML = '';
    legend.hidden = true;

    var productCount = series.length ? series[0].known + series[0].missing : 0;
    var partial = series.some(function (p) { return p.missing > 0; });
    var complete = series.some(function (p) { return p.missing === 0; });
    var changes = compositionChangeIndexes(series).length;
    if (!partial) return;

    function addItem(markClass, text) {
      var li = document.createElement('li');
      li.className = 'fav-dashboard__legend-item';
      var mark = document.createElement('span');
      mark.className = 'fav-dashboard__legend-mark ' + markClass;
      mark.setAttribute('aria-hidden', 'true');
      li.appendChild(mark);
      li.appendChild(document.createTextNode(text));
      legend.appendChild(li);
    }

    if (complete) {
      addItem('fav-dashboard__legend-mark--solid', '관심 제품 ' + productCount + '개 전부 합산');
    }
    addItem('fav-dashboard__legend-mark--dashed', '일부만 합산 (나머지는 시세 기록 시작 전)');
    if (changes) addItem('fav-dashboard__legend-mark--point', '합산 구성이 바뀐 날 ' + changes + '곳');
    legend.hidden = false;
  }

  function renderDashboard() {
    var panel = ensureDashboard();
    if (!panel) return;
    destroyDashboardChart();
    dashboardSeries = null;
    panel.hidden = false;
    panel.innerHTML = '';
    panel.setAttribute('data-fav-count', String(favoriteSet.size));

    var byId = {};
    getCardsData().forEach(function (c) { byId[c.id] = c; });
    var items = [];
    favoriteSet.forEach(function (pid) { if (byId[pid]) items.push(byId[pid]); });
    var priced = items.filter(function (c) { return c.median != null; });
    var total = priced.reduce(function (acc, c) { return acc + c.median; }, 0);

    var wrap = document.createElement('div');
    var kicker = document.createElement('span');
    kicker.className = 'section-kicker';
    kicker.textContent = 'My watchlist value';
    var title = document.createElement('h2');
    title.className = 'section-heading';
    title.textContent = '관심 목록 가치';
    wrap.appendChild(kicker);
    wrap.appendChild(title);
    panel.appendChild(wrap);

    // 절대액은 여기 그대로 남는다. 차트 기본 축을 지수로 바꿔도 "지금 내
    // 컬렉션이 얼마짜리인가"는 사용자가 가장 보고 싶어 하는 숫자라서다.
    var stats = document.createElement('div');
    stats.className = 'fav-dashboard__stats';
    stats.id = 'fav-dashboard-stats';
    stats.appendChild(buildDashStat(String(items.length) + '개', '관심 모델'));
    stats.appendChild(buildDashStat(formatUsd(total), '현재 중앙값 합산 (' + priced.length + '개 기준)'));
    panel.appendChild(stats);

    panel.appendChild(buildViewToggle());
    syncViewButtons();

    var chartWrap = document.createElement('div');
    chartWrap.className = 'fav-dashboard__chart';
    var canvas = document.createElement('canvas');
    canvas.id = 'fav-dashboard-canvas';
    chartWrap.appendChild(canvas);
    panel.appendChild(chartWrap);

    var legend = document.createElement('ul');
    legend.className = 'fav-dashboard__legend';
    legend.id = 'fav-dashboard-legend';
    legend.hidden = true;
    panel.appendChild(legend);

    var note = document.createElement('p');
    note.className = 'fav-dashboard__note';
    note.id = 'fav-dashboard-note';
    note.textContent = '추이 데이터를 불러오는 중...';
    panel.appendChild(note);

    loadDashboardTrend(priced.map(function (c) { return c.id; }));
  }

  function fetchHistory(pid) {
    return fetch('data/products/' + encodeURIComponent(pid) + '.json')
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }

  // Chart.js 온디맨드 로더는 js/site.js가 window.nikonValueChartLoader로 공개한다
  // (비교 페이지와 같은 구현 하나만 쓰기 위해 옮겼다). site.js → auth.js 로드
  // 순서는 nikon_value/sitegen/pages.py가 소유하므로 여기서는 항상 준비돼 있다.
  function ensureChartJs() {
    return window.nikonValueChartLoader.ensure();
  }

  var COMPOSITION_MARK_COLOR = '#c08400'; // 제품 페이지 이동평균선과 같은 강조색

  function formatIndex(value) {
    return Number(value).toFixed(1);
  }

  function renderTrendChart(series) {
    var canvas = document.getElementById('fav-dashboard-canvas');
    if (!canvas || !window.Chart) return;
    destroyDashboardChart();

    var indexed = dashboardView === 'indexed';
    var changeSet = {};
    compositionChangeIndexes(series).forEach(function (i) { changeSet[i] = true; });
    var basePointRadius = series.length < 60 ? 2 : 0;

    dashboardChart = new window.Chart(canvas.getContext('2d'), {
      type: 'line',
      data: {
        labels: series.map(function (p) { return p.date; }),
        datasets: [{
          label: indexed ? '합산 지수 (첫날=100)' : '합산 중앙값 (USD)',
          data: series.map(function (p) { return indexed ? p.index : Math.round(p.total); }),
          borderColor: '#1d1d1f',
          backgroundColor: 'rgba(29, 29, 31, 0.08)',
          // 지수는 변동률 축이라 0까지 칠하면 면적이 의미를 갖는 것처럼 보인다.
          // 절대액일 때만 채운다(비교 페이지의 지수 보기와 같은 처리).
          fill: !indexed,
          tension: 0.25,
          // 구성이 바뀐 날은 눈에 띄게 찍어 "여기서 합산 대상이 달라졌다"를 알린다.
          pointRadius: series.map(function (p, i) { return changeSet[i] ? 4 : basePointRadius; }),
          pointHoverRadius: 5,
          pointBackgroundColor: series.map(function (p, i) { return changeSet[i] ? COMPOSITION_MARK_COLOR : '#1d1d1f'; }),
          pointBorderColor: series.map(function (p, i) { return changeSet[i] ? COMPOSITION_MARK_COLOR : '#1d1d1f'; }),
          // 아직 일부 제품만 합산된 구간은 점선 — 같은 선이라도 뜻이 다르다.
          // (Chart.js는 콜백이 undefined를 돌려주면 데이터셋 기본값을 쓴다)
          segment: {
            borderDash: function (ctx) {
              var point = series[ctx.p1DataIndex];
              return point && point.missing > 0 ? [5, 4] : undefined;
            }
          }
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (item) {
                var point = series[item.dataIndex];
                if (!point) return null;
                return indexed
                  ? '지수 ' + formatIndex(point.index) + ' (첫날=100)'
                  : '합산액 ' + formatUsd(point.total);
              },
              // 두 축을 항상 함께 보여 준다. 지수만 보면 "얼마짜리인가"를 알 수
              // 없고, 절대액만 보면 그 금액이 몇 개짜리 합인지 알 수 없다.
              afterBody: function (items) {
                var point = series[items[0].dataIndex];
                if (!point) return null;
                var lines = [
                  indexed
                    ? '합산액 ' + formatUsd(point.total)
                    : '지수 ' + formatIndex(point.index) + ' (첫날=100)'
                ];
                lines.push(point.missing
                  ? '합산에 들어간 제품 ' + point.known + '개 (' + point.missing + '개는 시세 기록 시작 전)'
                  : '합산에 들어간 제품 ' + point.known + '개 (전부 포함)');
                return lines;
              }
            }
          }
        },
        scales: {
          x: { ticks: { maxTicksLimit: 8 } },
          y: {
            ticks: {
              callback: function (v) {
                return indexed ? formatIndex(v) : '$' + Number(v).toLocaleString('en-US');
              }
            }
          }
        }
      }
    });
  }

  // 축 토글 시 다시 fetch하지 않고 이미 만든 시계열만 다시 그린다.
  function applyDashboardView() {
    syncViewButtons();
    if (!dashboardSeries) return;
    var note = document.getElementById('fav-dashboard-note');
    if (note) note.textContent = buildSummedSeriesNote(dashboardSeries, { mode: dashboardView });
    renderTrendChart(dashboardSeries);
  }

  function loadDashboardTrend(ids) {
    var note = document.getElementById('fav-dashboard-note');
    if (!ids.length) {
      if (note) note.textContent = '시세 데이터가 있는 관심 제품이 없습니다.';
      return;
    }
    Promise.all(ids.map(fetchHistory)).then(function (histories) {
      var usable = histories.filter(function (h) { return h && h.length; });
      var series = buildSummedSeries(usable);
      if (series.length < 2) {
        if (note) note.textContent = '추이를 그릴 데이터가 아직 부족합니다.';
        return;
      }
      ensureChartJs().then(function (ok) {
        if (!ok) {
          if (note) note.textContent = '차트 라이브러리를 불러오지 못했습니다.';
          return;
        }
        dashboardSeries = series;
        var modes = document.getElementById('fav-dashboard-modes');
        if (modes) modes.hidden = false;
        // 구성 변화를 걷어낸 기간 변동률. 합산액 차이로는 구할 수 없는 값이라
        // (중간에 제품이 늘어난 만큼이 섞인다) 지수 끝값에서 뽑는다.
        var stats = document.getElementById('fav-dashboard-stats');
        var change = series[series.length - 1].index - 100;
        if (stats) {
          // 관심 항목이 빠르게 바뀌면 이전 fetch가 늦게 도착할 수 있다.
          // 칩이 두 개 붙지 않게 항상 마지막 결과로 교체한다.
          var previousChange = document.getElementById('fav-dashboard-change');
          if (previousChange) previousChange.remove();
          var changeStat = buildDashStat(
            (change > 0 ? '+' : '') + change.toFixed(1) + '%',
            '첫날 대비 시세 변동 (구성 변화 보정)'
          );
          changeStat.id = 'fav-dashboard-change';
          stats.appendChild(changeStat);
        }
        renderDashboardLegend(series);
        applyDashboardView();
      });
    });
  }

  // --- Close dropdown on outside click ---
  document.addEventListener('click', function () {
    var menu = document.getElementById('login-menu');
    if (menu) menu.hidden = true;
  });

  // --- Observe product grid for card creation ---
  function observeGrid() {
    var grid = document.getElementById('product-grid');
    if (!grid) return;
    var observer = new MutationObserver(function (mutations) {
      // Check if real product cards (not skeletons) have been added
      var hasRealCards = grid.querySelector('.product-card[data-product-id]');
      if (hasRealCards && !cardsReady) {
        cardsReady = true;
        observer.disconnect();
        onCardsReady();
      }
    });
    observer.observe(grid, { childList: true });
    // Also check immediately in case cards are already there
    if (grid.querySelector('.product-card[data-product-id]')) {
      cardsReady = true;
      onCardsReady();
    }
  }

  function onCardsReady() {
    if (currentUser) {
      injectFavoriteButtons();
      showFavoriteButtons(true);
      updateAllFavoriteButtons();
    } else {
      injectFavoriteButtons();
      showFavoriteButtons(false);
    }
  }

  // --- Init ---
  function init() {
    checkHashToken();
    observeGrid();

    var token = getToken();
    if (!token) {
      renderLoggedOut();
      return;
    }

    fetchMe().then(function (user) {
      if (!user) { renderLoggedOut(); return; }
      currentUser = user;
      renderLoggedIn(user);
      return fetchFavorites();
    }).then(function (favs) {
      favoriteSet = new Set(favs || []);
      updateAllFavoriteButtons();
      refreshCatalog();
      initAlertPanel();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
