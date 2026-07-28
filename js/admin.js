(function () {
// HTML 이스케이프는 js/site.js가 window.nikonValueShared로 공개하는 공용 모듈
// 하나만 쓴다(admin.html이 admin.js보다 먼저 site.js를 로드한다).
// 과거 admin.js의 esc()는 DOM textContent 기반이라 큰따옴표를 그대로 흘려서
// title="${esc(...)}" / value="${esc(...)}" 같은 속성 삽입 지점에서 속성을
// 탈출할 수 있었다. 통일된 구현은 & < > " ' 를 모두 이스케이프한다.
const esc = window.nikonValueShared.escapeHtml;

let catalog = null;
let activeCategoryIndex = null;
let unsaved = false;
let adminToken = null;

function getActiveCategory() {
  if (!catalog || activeCategoryIndex === null) return null;
  return catalog.categories[activeCategoryIndex] || null;
}

function updateActionState() {
  const hasActiveCategory = !!getActiveCategory();
  document.getElementById('fetch-category-btn').disabled = !hasActiveCategory;
}

function setTaskPanel(title, meta, logText) {
  document.getElementById('task-title').textContent = title || '최근 작업';
  document.getElementById('task-meta').textContent = meta || '';
  document.getElementById('task-log').textContent = logText || '';
  document.getElementById('task-panel').hidden = false;
}

function clearTaskPanel() {
  document.getElementById('task-panel').hidden = true;
  document.getElementById('task-title').textContent = '최근 작업';
  document.getElementById('task-meta').textContent = '';
  document.getElementById('task-log').textContent = '';
}

function setButtonBusy(buttonId, busy, busyText, idleText) {
  const btn = document.getElementById(buttonId);
  if (!btn) return;
  if (busy) {
    btn.dataset.idleText = idleText || btn.textContent;
    btn.disabled = true;
    btn.textContent = busyText;
    return;
  }
  btn.disabled = false;
  btn.textContent = btn.dataset.idleText || idleText || btn.textContent;
}

async function ensureSession() {
  if (adminToken) return adminToken;
  const resp = await fetch('/api/session', { cache: 'no-store' });
  if (!resp.ok) throw new Error(`session ${resp.status}`);
  const result = await resp.json();
  adminToken = result.token;
  return adminToken;
}

async function apiFetch(url, options = {}) {
  const token = await ensureSession();
  const headers = new Headers(options.headers || {});
  headers.set('X-Admin-Token', token);
  return fetch(url, { ...options, headers });
}

// --- API ---
async function loadCatalog(options = {}) {
  const { silent = false } = options;
  try {
    const resp = await apiFetch('/api/catalog', { cache: 'no-store' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    catalog = await resp.json();
    unsaved = false;
    updateUnsaved();
    renderSidebar();
    updateActionState();
    if (activeCategoryIndex !== null && activeCategoryIndex < catalog.categories.length) {
      renderProducts();
    } else {
      activeCategoryIndex = null;
      document.getElementById('content').innerHTML = '<div class="empty-state"><p>왼쪽에서 카테고리를 선택하세요</p></div>';
    }
    if (!silent) showToast('카탈로그 로드 완료');
  } catch (e) {
    showToast('로드 실패: ' + e.message, true);
    throw e;
  }
}

async function saveCatalog(options = {}) {
  const { silent = false } = options;
  try {
    const resp = await apiFetch('/api/catalog', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(catalog),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const result = await resp.json();
    unsaved = false;
    updateUnsaved();
    if (!silent) showToast('저장 완료 (백업: ' + result.backup + ')');
    return result;
  } catch (e) {
    showToast('저장 실패: ' + e.message, true);
    throw e;
  }
}

async function ensureSavedBeforeTask() {
  if (!unsaved) return;
  await saveCatalog({ silent: true });
}

async function runTask({ buttonId, idleText, busyText, title, url, body, reloadCatalog = false, successToast }) {
  setButtonBusy(buttonId, true, busyText, idleText);
  try {
    await ensureSavedBeforeTask();
    const resp = await apiFetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const result = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(result.error || `HTTP ${resp.status}`);
    if (reloadCatalog) await loadCatalog({ silent: true });
    const meta = result.detail || '';
    const logText = [result.stdout || '', result.stderr || ''].filter(Boolean).join('\n');
    setTaskPanel(title, meta, logText);
    showToast(successToast || result.detail || '작업 완료');
    return result;
  } catch (e) {
    setTaskPanel(title, '실패', e.message);
    showToast(`${title} 실패: ${e.message}`, true);
    throw e;
  } finally {
    setButtonBusy(buttonId, false, busyText, idleText);
    updateActionState();
  }
}

function markUnsaved() {
  unsaved = true;
  updateUnsaved();
}

function updateUnsaved() {
  document.getElementById('unsaved-dot').classList.toggle('show', unsaved);
  document.getElementById('save-btn').textContent = unsaved ? '저장 *' : '저장';
}

// --- Sidebar ---
function renderSidebar() {
  const list = document.getElementById('sidebar-list');
  list.innerHTML = '';
  let total = 0;

  catalog.categories.forEach((cat, i) => {
    total += cat.products.length;
    const item = document.createElement('div');
    item.className = 'sidebar-item' + (i === activeCategoryIndex ? ' active' : '');
    item.innerHTML = `
      <div class="sidebar-item-info">
        <span class="sidebar-item-name">${esc(cat.name_ko)}</span>
        <span class="sidebar-item-count">${cat.products.length}개 제품</span>
      </div>
      <div class="sidebar-actions">
        <button class="btn-icon" onclick="event.stopPropagation(); showCategoryModal(${i})" title="편집">&#9998;</button>
        <button class="btn-icon danger" onclick="event.stopPropagation(); deleteCategory(${i})" title="삭제">&#128465;</button>
      </div>
    `;
    item.addEventListener('click', () => {
      activeCategoryIndex = i;
      renderSidebar();
      renderProducts();
      updateActionState();
    });
    list.appendChild(item);
  });

  document.getElementById('total-stats').textContent = `${catalog.categories.length}개 카테고리, ${total}개 제품`;
}

// --- Products Table ---
function renderProducts() {
  const content = document.getElementById('content');
  if (activeCategoryIndex === null || !catalog) {
    content.innerHTML = '<div class="empty-state"><p>왼쪽에서 카테고리를 선택하세요</p></div>';
    return;
  }

  const cat = catalog.categories[activeCategoryIndex];
  const search = document.getElementById('search-input').value.toLowerCase().trim();

  let products = cat.products;
  if (search) {
    products = products.filter(p =>
      p.id.toLowerCase().includes(search) ||
      p.name_ko.toLowerCase().includes(search) ||
      p.name_en.toLowerCase().includes(search) ||
      p.query.toLowerCase().includes(search)
    );
  }

  // Group by subcategory
  const subcats = cat.subcategories || [];
  const hasSubcats = subcats.length > 0;

  let html = `
    <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:16px;">
      <div>
        <h2 style="font-size:1.2rem; font-weight:700;">${esc(cat.name_ko)} <span style="color:var(--text2); font-weight:400; font-size:0.9rem;">${esc(cat.name_en)}</span></h2>
        <div class="stats-bar">
          <span><strong>${products.length}</strong> 제품${search ? ' (검색결과)' : ''}</span>
          ${hasSubcats ? `<span>${subcats.length} 서브카테고리</span>` : ''}
        </div>
      </div>
      <div style="display:flex; gap:8px;">
        <button class="btn btn-outline" id="fetch-current-category-inline-btn" onclick="fetchActiveCategory()">이 카테고리 수집</button>
        <button class="btn btn-primary" onclick="showProductModal()">+ 제품 추가</button>
      </div>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th style="width:40px">#</th>
          <th>ID</th>
          <th>한국어명</th>
          <th>영어명</th>
          <th>쿼리</th>
          <th class="cell-price">가격범위</th>
          <th style="width:110px"></th>
        </tr></thead>
        <tbody>
  `;

  if (hasSubcats && !search) {
    // Grouped rendering
    const sortedSubcats = [...subcats].sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
    for (const sub of sortedSubcats) {
      const subProducts = products.filter(p => p.subcategory === sub.id);
      if (subProducts.length === 0) continue;
      html += `<tr class="subcat-header"><td colspan="7">${esc(sub.name_ko)} (${esc(sub.name_en)}) - ${subProducts.length}개</td></tr>`;
      subProducts.forEach((p, pi) => {
        const realIndex = cat.products.indexOf(p);
        html += productRow(p, realIndex, pi + 1);
      });
    }
    // Uncategorized
    const uncategorized = products.filter(p => !subcats.some(s => s.id === p.subcategory));
    if (uncategorized.length > 0) {
      html += `<tr class="subcat-header"><td colspan="7">미분류 - ${uncategorized.length}개</td></tr>`;
      uncategorized.forEach((p, pi) => {
        const realIndex = cat.products.indexOf(p);
        html += productRow(p, realIndex, pi + 1);
      });
    }
  } else {
    products.forEach((p, pi) => {
      const realIndex = cat.products.indexOf(p);
      html += productRow(p, realIndex, pi + 1);
    });
  }

  html += '</tbody></table></div>';
  content.innerHTML = html;
}

function productRow(p, realIndex, displayNum) {
  const priceRange = `$${p.min_price} - $${p.max_price}`;
  const extra = [];
  if (p.release_year) extra.push(p.release_year);
  if (p.focal_length_min) extra.push(p.focal_length_min + 'mm');
  const extraStr = extra.length > 0 ? ` <span style="color:var(--text2); font-size:0.75rem;">(${extra.join(', ')})</span>` : '';

  return `<tr>
    <td style="color:var(--text2)">${displayNum}</td>
    <td class="cell-id">${esc(p.id)}</td>
    <td>${esc(p.name_ko)}${extraStr}</td>
    <td style="font-size:0.8rem;">${esc(p.name_en)}</td>
    <td style="font-size:0.8rem; max-width:200px; overflow:hidden; text-overflow:ellipsis;" title="${esc(p.query)}">${esc(p.query)}</td>
    <td class="cell-price">${priceRange}</td>
    <td class="cell-actions">
      <button class="btn-icon" onclick="fetchSingleProduct(${realIndex})" title="시세 수집">&#8635;</button>
      <button class="btn-icon" onclick="showProductModal(${realIndex})" title="편집">&#9998;</button>
      <button class="btn-icon danger" onclick="deleteProduct(${realIndex})" title="삭제">&#128465;</button>
    </td>
  </tr>`;
}

// --- Category Modal ---
let tempSubcats = [];

function showCategoryModal(editIndex) {
  const isEdit = editIndex !== undefined;
  document.getElementById('category-modal-title').textContent = isEdit ? '카테고리 편집' : '카테고리 추가';
  document.getElementById('cat-edit-index').value = isEdit ? editIndex : '';

  if (isEdit) {
    const cat = catalog.categories[editIndex];
    document.getElementById('cat-id').value = cat.id;
    document.getElementById('cat-id').disabled = true;
    document.getElementById('cat-name-ko').value = cat.name_ko;
    document.getElementById('cat-name-en').value = cat.name_en;
    tempSubcats = (cat.subcategories || []).map(s => ({ ...s }));
  } else {
    document.getElementById('cat-id').value = '';
    document.getElementById('cat-id').disabled = false;
    document.getElementById('cat-name-ko').value = '';
    document.getElementById('cat-name-en').value = '';
    tempSubcats = [];
  }
  renderSubcatChips();
  openModal('category-modal');
}

function renderSubcatChips() {
  const container = document.getElementById('cat-subcats');
  container.innerHTML = tempSubcats.map((s, i) =>
    `<span class="subcat-chip">${esc(s.name_ko)} (${esc(s.id)}) <span class="chip-remove" onclick="removeSubcatChip(${i})">&times;</span></span>`
  ).join('');
}

function addSubcatChip() {
  const id = document.getElementById('subcat-id-input').value.trim();
  const ko = document.getElementById('subcat-ko-input').value.trim();
  const en = document.getElementById('subcat-en-input').value.trim();
  if (!id || !ko) return showToast('서브카테고리 ID와 한국어명은 필수입니다', true);
  if (tempSubcats.some(s => s.id === id)) return showToast('중복된 서브카테고리 ID', true);
  tempSubcats.push({ id, name_ko: ko, name_en: en || ko, sort_order: tempSubcats.length + 1 });
  renderSubcatChips();
  document.getElementById('subcat-id-input').value = '';
  document.getElementById('subcat-ko-input').value = '';
  document.getElementById('subcat-en-input').value = '';
}

function removeSubcatChip(i) {
  tempSubcats.splice(i, 1);
  renderSubcatChips();
}

function saveCategory() {
  const editIndex = document.getElementById('cat-edit-index').value;
  const isEdit = editIndex !== '';
  const id = document.getElementById('cat-id').value.trim();
  const name_ko = document.getElementById('cat-name-ko').value.trim();
  const name_en = document.getElementById('cat-name-en').value.trim();

  if (!id || !name_ko || !name_en) return showToast('모든 필드를 입력하세요', true);
  if (!isEdit && catalog.categories.some(c => c.id === id)) return showToast('중복된 카테고리 ID', true);

  if (isEdit) {
    const cat = catalog.categories[parseInt(editIndex)];
    cat.name_ko = name_ko;
    cat.name_en = name_en;
    cat.subcategories = tempSubcats;
  } else {
    catalog.categories.push({
      id,
      name_ko,
      name_en,
      subcategories: tempSubcats,
      products: [],
    });
    activeCategoryIndex = catalog.categories.length - 1;
  }

  markUnsaved();
  renderSidebar();
  renderProducts();
  closeModal('category-modal');
  showToast(isEdit ? '카테고리 수정됨' : '카테고리 추가됨');
}

function deleteCategory(index) {
  const cat = catalog.categories[index];
  showDeleteConfirm(
    `"${cat.name_ko}" 카테고리와 포함된 ${cat.products.length}개 제품을 삭제하시겠습니까?`,
    () => {
      catalog.categories.splice(index, 1);
      if (activeCategoryIndex === index) activeCategoryIndex = null;
      else if (activeCategoryIndex > index) activeCategoryIndex--;
      markUnsaved();
      renderSidebar();
      renderProducts();
      showToast('카테고리 삭제됨');
    }
  );
}

// --- Product Modal ---
function showProductModal(editIndex) {
  const isEdit = editIndex !== undefined;
  const cat = catalog.categories[activeCategoryIndex];
  document.getElementById('product-modal-title').textContent = isEdit ? '제품 편집' : '제품 추가';
  document.getElementById('prod-edit-index').value = isEdit ? editIndex : '';

  // Populate subcategory select
  const subcatSelect = document.getElementById('prod-subcategory');
  subcatSelect.innerHTML = '<option value="">(없음)</option>';
  (cat.subcategories || []).forEach(s => {
    subcatSelect.innerHTML += `<option value="${esc(s.id)}">${esc(s.name_ko)}</option>`;
  });

  if (isEdit) {
    const p = cat.products[editIndex];
    document.getElementById('prod-id').value = p.id;
    document.getElementById('prod-id').disabled = true;
    document.getElementById('prod-name-ko').value = p.name_ko;
    document.getElementById('prod-name-en').value = p.name_en;
    document.getElementById('prod-query').value = p.query;
    document.getElementById('prod-category-id').value = p.category_id;
    document.getElementById('prod-release-year').value = p.release_year || '';
    document.getElementById('prod-focal-length').value = p.focal_length_min || '';
    document.getElementById('prod-min-price').value = p.min_price;
    document.getElementById('prod-max-price').value = p.max_price;
    subcatSelect.value = p.subcategory || '';
  } else {
    document.getElementById('prod-id').value = '';
    document.getElementById('prod-id').disabled = false;
    document.getElementById('prod-name-ko').value = '';
    document.getElementById('prod-name-en').value = '';
    document.getElementById('prod-query').value = '';
    document.getElementById('prod-category-id').value = '31388';
    document.getElementById('prod-release-year').value = '';
    document.getElementById('prod-focal-length').value = '';
    document.getElementById('prod-min-price').value = '';
    document.getElementById('prod-max-price').value = '';
    subcatSelect.value = '';
  }

  openModal('product-modal');
}

function saveProduct() {
  const editIndex = document.getElementById('prod-edit-index').value;
  const isEdit = editIndex !== '';
  const cat = catalog.categories[activeCategoryIndex];

  const id = document.getElementById('prod-id').value.trim();
  const name_ko = document.getElementById('prod-name-ko').value.trim();
  const name_en = document.getElementById('prod-name-en').value.trim();
  const query = document.getElementById('prod-query').value.trim();
  const category_id = document.getElementById('prod-category-id').value;
  const min_price = parseInt(document.getElementById('prod-min-price').value);
  const max_price = parseInt(document.getElementById('prod-max-price').value);
  const subcategory = document.getElementById('prod-subcategory').value;
  const release_year = document.getElementById('prod-release-year').value ? parseInt(document.getElementById('prod-release-year').value) : null;
  const focal_length_min = document.getElementById('prod-focal-length').value ? parseInt(document.getElementById('prod-focal-length').value) : null;

  if (!id || !name_ko || !name_en || !query) return showToast('필수 필드를 입력하세요 (ID, 이름, 쿼리)', true);
  if (isNaN(min_price) || isNaN(max_price)) return showToast('가격 범위를 입력하세요', true);
  if (min_price >= max_price) return showToast('최소 가격이 최대 가격보다 작아야 합니다', true);

  // Check duplicate ID across all categories
  if (!isEdit) {
    for (const c of catalog.categories) {
      if (c.products.some(p => p.id === id)) {
        return showToast('중복된 제품 ID: ' + id, true);
      }
    }
  }

  const product = { id, name_ko, name_en };
  if (release_year) product.release_year = release_year;
  if (focal_length_min) product.focal_length_min = focal_length_min;
  if (subcategory) product.subcategory = subcategory;
  product.query = query;
  product.category_id = category_id;
  product.min_price = min_price;
  product.max_price = max_price;

  if (isEdit) {
    cat.products[parseInt(editIndex)] = product;
  } else {
    cat.products.push(product);
  }

  markUnsaved();
  renderSidebar();
  renderProducts();
  closeModal('product-modal');
  showToast(isEdit ? '제품 수정됨' : '제품 추가됨');
}

function deleteProduct(index) {
  const cat = catalog.categories[activeCategoryIndex];
  const p = cat.products[index];
  showDeleteConfirm(
    `"${p.name_ko}" (${p.id}) 제품을 삭제하시겠습니까?`,
    () => {
      cat.products.splice(index, 1);
      markUnsaved();
      renderSidebar();
      renderProducts();
      showToast('제품 삭제됨');
    }
  );
}

async function fetchProducts(productIds, label) {
  return runTask({
    buttonId: 'fetch-category-btn',
    idleText: '카테고리 수집',
    busyText: '수집 중...',
    title: label || '시세 수집',
    url: '/api/fetch-prices',
    body: { product_ids: productIds },
    reloadCatalog: true,
    successToast: '시세 수집 완료',
  });
}

async function fetchActiveCategory() {
  const cat = getActiveCategory();
  if (!cat) {
    showToast('먼저 카테고리를 선택하세요', true);
    return;
  }
  await fetchProducts(cat.products.map(p => p.id), `${cat.name_ko} 시세 수집`);
}

async function fetchSingleProduct(index) {
  const cat = getActiveCategory();
  if (!cat) return;
  const product = cat.products[index];
  if (!product) return;
  await fetchProducts([product.id], `${product.name_ko} 시세 수집`);
}

async function buildSite() {
  await runTask({
    buttonId: 'build-btn',
    idleText: '사이트 빌드',
    busyText: '빌드 중...',
    title: '정적 사이트 빌드',
    url: '/api/build-site',
    // Pages가 artifact 배포로 전환되어 루트 반영은 더 이상 쓰지 않는다.
    body: { publish_root: false },
    successToast: '사이트 빌드 완료',
  });
}

// --- Modal helpers ---
function openModal(id) {
  document.getElementById(id).classList.add('open');
}

function closeModal(id) {
  document.getElementById(id).classList.remove('open');
}

function showDeleteConfirm(message, onConfirm) {
  document.getElementById('delete-message').textContent = message;
  const btn = document.getElementById('delete-confirm-btn');
  const newBtn = btn.cloneNode(true);
  btn.parentNode.replaceChild(newBtn, btn);
  newBtn.addEventListener('click', () => {
    onConfirm();
    closeModal('delete-modal');
  });
  openModal('delete-modal');
}

// --- Toast ---
let toastTimer;
function showToast(msg, isError) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.className = 'toast'; }, 3000);
}

// --- Git Push ---
async function gitPush() {
  const message = prompt('커밋 메시지:', '제품 카탈로그 업데이트');
  if (message === null) return;
  try {
    const result = await runTask({
      buttonId: 'push-btn',
      idleText: 'Git Push',
      busyText: 'Pushing...',
      title: 'Git 커밋 및 푸시',
      url: '/api/git-push',
      body: { message },
      successToast: 'Git Push 완료',
    });
    if (result.skipped) showToast('변경사항 없음 (이미 최신)');
  } catch (e) {
    // runTask already surfaced the error
  }
}

// --- Keyboard shortcuts ---
document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    saveCatalog();
  }
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay.open').forEach(m => m.classList.remove('open'));
  }
});

// Close modal on overlay click
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) overlay.classList.remove('open');
  });
});

// Warn before unload
window.addEventListener('beforeunload', (e) => {
  if (unsaved) {
    e.preventDefault();
    e.returnValue = '';
  }
});

// --- Init ---
ensureSession()
  .then(loadCatalog)
  .catch((e) => {
    showToast('Admin 세션 초기화 실패: ' + e.message, true);
  });

// Expose functions referenced by inline event handler attributes in admin.html.
window.clearTaskPanel = clearTaskPanel;
window.loadCatalog = loadCatalog;
window.saveCatalog = saveCatalog;
window.renderProducts = renderProducts;
window.showCategoryModal = showCategoryModal;
window.addSubcatChip = addSubcatChip;
window.removeSubcatChip = removeSubcatChip;
window.saveCategory = saveCategory;
window.deleteCategory = deleteCategory;
window.showProductModal = showProductModal;
window.saveProduct = saveProduct;
window.deleteProduct = deleteProduct;
window.fetchActiveCategory = fetchActiveCategory;
window.fetchSingleProduct = fetchSingleProduct;
window.buildSite = buildSite;
window.closeModal = closeModal;
window.gitPush = gitPush;
})();
