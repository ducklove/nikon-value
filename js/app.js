// 메인 페이지 로직
(function () {
  "use strict";

  const CATALOG_URL = "data/catalog.json";

  let catalogData = null;
  let activeCategory = null;

  async function init() {
    try {
      const resp = await fetch(CATALOG_URL);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      catalogData = await resp.json();

      document.getElementById("loading").hidden = true;
      document.getElementById("updated-date").textContent =
        `최종 업데이트: ${catalogData.updated}`;

      renderCategoryTabs();
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

  function renderProducts() {
    const grid = document.getElementById("product-grid");
    grid.innerHTML = "";

    const categories = activeCategory
      ? catalogData.categories.filter((c) => c.id === activeCategory)
      : catalogData.categories;

    for (const cat of categories) {
      for (const product of cat.products) {
        const card = createProductCard(product);
        grid.appendChild(card);
      }
    }
  }

  function createProductCard(product) {
    const a = document.createElement("a");
    a.className = "product-card";
    a.href = `product.html?id=${product.id}`;

    const nameDiv = document.createElement("div");
    nameDiv.className = "product-card__name";
    nameDiv.textContent = product.name_ko;

    const nameEnDiv = document.createElement("div");
    nameEnDiv.className = "product-card__name-en";
    nameEnDiv.textContent = product.name_en;

    const priceDiv = document.createElement("div");
    if (product.median !== null) {
      priceDiv.className = "product-card__price";
      priceDiv.textContent = `$${product.median.toLocaleString()}`;
    } else {
      priceDiv.className = "product-card__price product-card__price--na";
      priceDiv.textContent = "데이터 없음";
    }

    const metaDiv = document.createElement("div");
    metaDiv.className = "product-card__meta";
    metaDiv.innerHTML = `<span>매물 ${product.count}개</span>`;

    const rangeDiv = document.createElement("div");
    rangeDiv.className = "product-card__range";
    if (product.q1 !== null && product.q3 !== null) {
      rangeDiv.textContent = `Q1-Q3: $${product.q1.toLocaleString()} - $${product.q3.toLocaleString()}`;
    }

    a.append(nameDiv, nameEnDiv, priceDiv, metaDiv, rangeDiv);
    return a;
  }

  init();
})();
