(function () {
  'use strict';

  // 빌드 시 페이지에 주입되는 meta 태그가 있으면 우선 사용한다 (환경별 API 주소).
  var apiBaseMeta = document.querySelector('meta[name="nikon-api-base"]');
  var API_BASE = (apiBaseMeta && apiBaseMeta.content) || 'https://cantabile.tplinkdns.com';
  var TOKEN_KEY = 'nikon-value-token';

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

  // --- Favorites tab ---
  function setupFavoritesTab() {
    var favTab = document.getElementById('favorites-tab');
    if (!favTab) return;
    favTab.addEventListener('click', function () {
      // Deactivate all tabs, activate this one
      document.querySelectorAll('.category-tab').forEach(function (t) { t.classList.remove('active'); t.setAttribute('aria-pressed', 'false'); });
      favTab.classList.add('active');
      favTab.setAttribute('aria-pressed', 'true');
      // Filter cards
      document.querySelectorAll('.product-card[data-product-id]').forEach(function (card) {
        var pid = card.getAttribute('data-product-id');
        card.style.display = favoriteSet.has(pid) ? '' : 'none';
      });
      // Update heading
      var ctx = document.getElementById('catalog-context');
      if (ctx) ctx.textContent = '관심 목록';
      var count = document.getElementById('visible-count');
      if (count) count.textContent = String(favoriteSet.size);
      // Hide empty state or show it
      var empty = document.getElementById('catalog-empty');
      if (empty) empty.hidden = favoriteSet.size > 0;
    });
  }

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
    var panel = ensureAlertPanel();
    if (!panel) return;
    panel.innerHTML = '';
    panel.appendChild(buildAlertTitle());
    panel.appendChild(buildAlertNote('로그인하면 중앙값이 목표가 이하로 내려갈 때 이메일 알림을 받을 수 있습니다.'));
  }

  function renderAlertPanelLoggedIn(pid) {
    var panel = ensureAlertPanel();
    if (!panel) return;
    panel.innerHTML = '';

    if (currentUser && !currentUser.email) {
      panel.appendChild(buildAlertTitle());
      panel.appendChild(buildAlertNote('계정에 이메일 주소가 없어 알림을 받을 수 없습니다.'));
      return;
    }

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
        ? '중앙값이 USD ' + existing.target_price + ' 이하로 내려가면 이메일로 알림이 발송됩니다.'
        : '중앙값이 목표가(USD) 이하로 내려가면 이메일로 알려드립니다.'
    ));

    if (existing && existing.triggered) {
      setAlertStatus('목표가 도달 알림이 발송된 상태입니다. 가격이 회복되면 다시 활성화됩니다.');
    }
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
        return;
      }
      alertMap = new Map(alerts.map(function (a) { return [a.product_id, a]; }));
      renderAlertPanelLoggedIn(pid);
    });
  }

  // --- Close dropdown on outside click ---
  document.addEventListener('click', function () {
    var menu = document.getElementById('login-menu');
    if (menu) menu.hidden = true;
  });

  // --- Utilities ---
  function escapeHtml(s) {
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

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
