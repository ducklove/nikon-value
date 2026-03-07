// 메인 페이지 로직
(function () {
  "use strict";

  const CATALOG_URL = "data/catalog.json";

  let catalogData = null;
  let activeCategory = null;
  let searchQuery = "";

  async function init() {
    try {
      const resp = await fetch(CATALOG_URL);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      catalogData = await resp.json();

      document.getElementById("loading").hidden = true;
      document.getElementById("updated-date").textContent =
        `최종 업데이트: ${catalogData.updated}`;

      renderCategoryTabs();
      initSearch();
      renderProducts();
    } catch (err) {
      console.error("Failed to load catalog:", err);
      document.getElementById("loading").hidden = true;
      document.getElementById("error").hidden = false;
    }
  }

  function renderCategoryTabs() {
    const tabsEl = document.getElementById("category-tabs");

    // "전체" 탭
    const allLi = document.createElement("li");
    const allBtn = document.createElement("button");
    allBtn.className = "category-tab active";
    allBtn.textContent = "전체";
    allBtn.addEventListener("click", () => {
      activeCategory = null;
      updateActiveTabs();
      renderProducts();
    });
    allLi.appendChild(allBtn);
    tabsEl.appendChild(allLi);

    for (const cat of catalogData.categories) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.className = "category-tab";
      btn.textContent = cat.name_ko;
      btn.dataset.categoryId = cat.id;
      btn.addEventListener("click", () => {
        activeCategory = cat.id;
        updateActiveTabs();
        renderProducts();
      });
      li.appendChild(btn);
      tabsEl.appendChild(li);
    }
  }

  function updateActiveTabs() {
    const tabs = document.querySelectorAll(".category-tab");
    tabs.forEach((tab) => {
      if (activeCategory === null) {
        tab.classList.toggle("active", !tab.dataset.categoryId);
      } else {
        tab.classList.toggle(
          "active",
          tab.dataset.categoryId === activeCategory
        );
      }
    });
  }

  function initSearch() {
    const input = document.getElementById("search-input");
    input.addEventListener("input", () => {
      searchQuery = input.value.trim().toLowerCase();
      renderProducts();
    });
  }

  function matchesSearch(product) {
    if (!searchQuery) return true;
    return (
      product.name_ko.toLowerCase().includes(searchQuery) ||
      product.name_en.toLowerCase().includes(searchQuery) ||
      product.id.toLowerCase().includes(searchQuery)
    );
  }

  const BODY_CATEGORIES = ["z-mount-bodies", "f-mount-dslr", "film-cameras"];

  function isLensCategory(catId) {
    return catId.endsWith("-lenses");
  }

  function sortProducts(products, categoryId) {
    const sorted = [...products];
    if (BODY_CATEGORIES.includes(categoryId)) {
      sorted.sort((a, b) => (b.release_year || 0) - (a.release_year || 0));
    } else if (isLensCategory(categoryId)) {
      sorted.sort(
        (a, b) => (a.focal_length_min || 0) - (b.focal_length_min || 0)
      );
    }
    return sorted;
  }

  function renderProducts() {
    const grid = document.getElementById("product-grid");
    grid.innerHTML = "";

    const categories = activeCategory
      ? catalogData.categories.filter((c) => c.id === activeCategory)
      : catalogData.categories;

    for (const cat of categories) {
      const filtered = cat.products.filter(matchesSearch);
      if (filtered.length === 0) continue;

      if (activeCategory && cat.subcategories && cat.subcategories.length > 0) {
        renderGroupedProducts(grid, cat, filtered);
      } else {
        const sorted = sortProducts(filtered, cat.id);
        for (const product of sorted) {
          const card = createProductCard(product, cat.id);
          grid.appendChild(card);
        }
      }
    }
  }

  function renderGroupedProducts(grid, category, filteredProducts) {
    const subcategories = [...category.subcategories].sort(
      (a, b) => a.sort_order - b.sort_order
    );

    for (const sub of subcategories) {
      const products = filteredProducts.filter(
        (p) => p.subcategory === sub.id
      );
      if (products.length === 0) continue;

      const header = document.createElement("div");
      header.className = "subcategory-header";
      header.textContent = sub.name_ko;
      grid.appendChild(header);

      const sorted = sortProducts(products, category.id);
      for (const product of sorted) {
        const card = createProductCard(product, category.id);
        grid.appendChild(card);
      }
    }
  }

  function createProductCard(product, categoryId) {
    const a = document.createElement("a");
    a.className = "product-card";
    a.href = `product.html?id=${product.id}`;

    // Thumbnail
    const thumbUrl =
      product.samples && product.samples.length > 0
        ? product.samples[0].image
        : "";
    if (thumbUrl) {
      const img = document.createElement("img");
      img.className = "product-card__thumb";
      img.src = thumbUrl;
      img.alt = product.name_en;
      img.loading = "lazy";
      a.appendChild(img);
    } else {
      const ph = document.createElement("div");
      ph.className = "product-card__thumb-placeholder";
      ph.textContent = "\u{1F4F7}";
      a.appendChild(ph);
    }

    // Body
    const body = document.createElement("div");
    body.className = "product-card__body";

    // Header (name + badge)
    const header = document.createElement("div");
    header.className = "product-card__header";

    const nameDiv = document.createElement("div");
    nameDiv.className = "product-card__name";
    nameDiv.textContent = product.name_ko;
    header.appendChild(nameDiv);

    if (BODY_CATEGORIES.includes(categoryId) && product.release_year) {
      const badge = document.createElement("span");
      badge.className = "product-card__badge";
      badge.textContent = product.release_year;
      header.appendChild(badge);
    } else if (isLensCategory(categoryId) && product.focal_length_min) {
      const badge = document.createElement("span");
      badge.className = "product-card__badge";
      badge.textContent = `${product.focal_length_min}mm`;
      header.appendChild(badge);
    }
    body.appendChild(header);

    const nameEnDiv = document.createElement("div");
    nameEnDiv.className = "product-card__name-en";
    nameEnDiv.textContent = product.name_en;
    body.appendChild(nameEnDiv);

    const priceDiv = document.createElement("div");
    if (product.median !== null) {
      priceDiv.className = "product-card__price";
      priceDiv.textContent = `$${product.median.toLocaleString()}`;
    } else {
      priceDiv.className = "product-card__price product-card__price--na";
      priceDiv.textContent = "데이터 없음";
    }
    body.appendChild(priceDiv);

    const metaDiv = document.createElement("div");
    metaDiv.className = "product-card__meta";
    metaDiv.innerHTML = `<span>매물 ${product.count}개</span>`;
    body.appendChild(metaDiv);

    const rangeDiv = document.createElement("div");
    rangeDiv.className = "product-card__range";
    if (product.q1 !== null && product.q3 !== null) {
      rangeDiv.textContent = `Q1-Q3: $${product.q1.toLocaleString()} - $${product.q3.toLocaleString()}`;
    }
    body.appendChild(rangeDiv);

    a.appendChild(body);
    return a;
  }

  init();
})();
