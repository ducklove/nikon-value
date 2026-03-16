(function () {
  'use strict';

  var API_BASE = 'https://cantabile.tplinkdns.com';
  var TOKEN_KEY = 'nikon-value-token';

  // --- Token management ---

  function getToken() {
    return localStorage.getItem(TOKEN_KEY);
  }

  function setToken(token) {
    localStorage.setItem(TOKEN_KEY, token);
  }

  function clearToken() {
    localStorage.removeItem(TOKEN_KEY);
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
        if (resp.status === 401) {
          clearToken();
          renderLoggedOut();
          return null;
        }
        return resp;
      })
      .catch(function (e) {
        console.warn('API server unreachable:', e.message);
        return null;
      });
  }

  function fetchMe() {
    return apiFetch('/api/me').then(function (resp) {
      if (!resp || !resp.ok) return null;
      return resp.json();
    });
  }

  function fetchFavorites() {
    return apiFetch('/api/favorites').then(function (resp) {
      if (!resp || !resp.ok) return [];
      return resp.json().then(function (data) {
        return data.favorites || [];
      });
    });
  }

  function addFavorite(productId) {
    return apiFetch('/api/favorites/' + encodeURIComponent(productId), { method: 'PUT' });
  }

  function removeFavorite(productId) {
    return apiFetch('/api/favorites/' + encodeURIComponent(productId), { method: 'DELETE' });
  }

  // --- State ---

  var currentUser = null;
  var favoriteSet = new Set();

  // --- UI rendering ---

  function renderLoggedIn(user) {
    var authArea = document.getElementById('auth-area');
    if (!authArea) return;
    authArea.innerHTML =
      '<span class="auth-user-name">' + escapeHtml(user.name || user.email || '사용자') + '</span>' +
      '<button class="auth-btn auth-btn--logout" id="logout-btn">로그아웃</button>';
    var logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) logoutBtn.addEventListener('click', handleLogout);
  }

  function renderLoggedOut() {
    currentUser = null;
    favoriteSet.clear();
    var authArea = document.getElementById('auth-area');
    if (!authArea) return;
    var returnTo = encodeURIComponent(window.location.pathname);
    authArea.innerHTML =
      '<div class="auth-login-dropdown">' +
        '<button class="auth-btn auth-btn--login" id="login-toggle">로그인</button>' +
        '<div class="auth-dropdown-menu" id="login-menu" hidden>' +
          '<a href="' + API_BASE + '/auth/google?return_to=' + returnTo + '" class="auth-dropdown-item">Google</a>' +
          '<a href="' + API_BASE + '/auth/naver?return_to=' + returnTo + '" class="auth-dropdown-item">Naver</a>' +
          '<a href="' + API_BASE + '/auth/kakao?return_to=' + returnTo + '" class="auth-dropdown-item">Kakao</a>' +
        '</div>' +
      '</div>';
    var loginToggle = document.getElementById('login-toggle');
    if (loginToggle) {
      loginToggle.addEventListener('click', function () {
        var menu = document.getElementById('login-menu');
        if (menu) menu.hidden = !menu.hidden;
      });
    }
    updateAllFavoriteButtons();
  }

  function updateAllFavoriteButtons() {
    document.querySelectorAll('[data-favorite-btn]').forEach(function (btn) {
      var pid = btn.getAttribute('data-product-id');
      var isActive = favoriteSet.has(pid);
      btn.classList.toggle('favorite--active', isActive);
      btn.textContent = isActive ? '\u2665' : '\u2661';
      btn.setAttribute('aria-label', isActive ? '관심 목록에서 제거' : '관심 목록에 추가');
    });
  }

  function handleFavoriteClick(e) {
    e.preventDefault();
    e.stopPropagation();
    var btn = e.currentTarget;
    var pid = btn.getAttribute('data-product-id');
    if (!currentUser) {
      var loginBtn = document.getElementById('login-toggle');
      if (loginBtn) loginBtn.click();
      return;
    }
    if (favoriteSet.has(pid)) {
      favoriteSet.delete(pid);
      btn.classList.remove('favorite--active');
      btn.textContent = '\u2661';
      removeFavorite(pid);
    } else {
      favoriteSet.add(pid);
      btn.classList.add('favorite--active');
      btn.textContent = '\u2665';
      addFavorite(pid);
    }
  }

  function handleLogout() {
    clearToken();
    currentUser = null;
    favoriteSet.clear();
    renderLoggedOut();
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // --- Init ---

  function injectFavoriteButtons() {
    document.querySelectorAll('.product-card[data-product-id]').forEach(function (card) {
      var pid = card.getAttribute('data-product-id');
      if (card.querySelector('[data-favorite-btn]')) return;
      var btn = document.createElement('button');
      btn.className = 'favorite-btn';
      btn.setAttribute('data-favorite-btn', '');
      btn.setAttribute('data-product-id', pid);
      btn.setAttribute('aria-label', '관심 목록에 추가');
      btn.textContent = '\u2661';
      btn.addEventListener('click', handleFavoriteClick);
      var body = card.querySelector('.product-card__body');
      if (body) body.prepend(btn);
    });
  }

  function init() {
    checkHashToken();
    injectFavoriteButtons();

    var token = getToken();
    if (!token) {
      renderLoggedOut();
      return;
    }

    fetchMe().then(function (user) {
      if (!user) {
        renderLoggedOut();
        return;
      }
      currentUser = user;
      renderLoggedIn(user);
      return fetchFavorites();
    }).then(function (favs) {
      if (favs && favs.length) {
        favoriteSet = new Set(favs);
        updateAllFavoriteButtons();
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
