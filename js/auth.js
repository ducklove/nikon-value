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
    return fetch(API_BASE + path, Object.assign({ headers: headers }, options || {}))
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
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
