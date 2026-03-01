// 제품 상세 페이지 로직
(function () {
  "use strict";

  const params = new URLSearchParams(window.location.search);
  const productId = params.get("id");

  let chartInstance = null;
  let historyData = [];
  let activePeriod = 180;

  async function init() {
    if (!productId) {
      showError();
      return;
    }

    try {
      // catalog.json에서 제품 기본 정보 로드
      const catalogResp = await fetch("data/catalog.json");
      if (!catalogResp.ok) throw new Error("catalog fetch failed");
      const catalog = await catalogResp.json();

      const product = findProduct(catalog, productId);
      if (!product) throw new Error("product not found");

      renderHeader(product);
      renderPriceSummary(product);
      renderListings(product.samples || []);

      // 시계열 데이터 로드
      try {
        const histResp = await fetch(`data/products/${productId}.json`);
        if (histResp.ok) {
          historyData = await histResp.json();
        }
      } catch {
        historyData = [];
      }

      document.getElementById("loading").hidden = true;
      document.getElementById("product-content").hidden = false;

      if (historyData.length > 1) {
        initChart();
      } else {
        document.getElementById("chart-empty").hidden = false;
      }

      initPeriodButtons();
    } catch (err) {
      console.error("Failed to load product:", err);
      showError();
    }
  }

  function findProduct(catalog, id) {
    for (const cat of catalog.categories) {
      for (const p of cat.products) {
        if (p.id === id) return p;
      }
    }
    return null;
  }

  function showError() {
    document.getElementById("loading").hidden = true;
    document.getElementById("error").hidden = false;
  }

  function renderHeader(product) {
    document.getElementById("product-title").textContent = product.name_ko;
    document.getElementById("product-subtitle").textContent = product.name_en;
    document.title = `${product.name_ko} - 니콘 중고 시세`;
  }

  function renderPriceSummary(product) {
    const fmt = (v) => (v !== null ? `$${v.toLocaleString()}` : "-");
    document.getElementById("price-median").textContent = fmt(product.median);
    document.getElementById("price-mean").textContent = fmt(product.mean);
    document.getElementById("price-min").textContent = fmt(product.min);
    document.getElementById("price-max").textContent = fmt(product.max);
    document.getElementById("price-count").textContent =
      product.count !== null ? product.count : "-";
    document.getElementById("price-iqr").textContent =
      product.q1 !== null
        ? `$${product.q1.toLocaleString()} - $${product.q3.toLocaleString()}`
        : "-";
  }

  function renderListings(samples) {
    const grid = document.getElementById("listings-grid");
    grid.innerHTML = "";

    if (samples.length === 0) {
      grid.innerHTML =
        '<p style="color:var(--color-text-secondary)">현재 매물 정보가 없습니다.</p>';
      return;
    }

    for (const item of samples) {
      const a = document.createElement("a");
      a.className = "listing-card";
      a.href = item.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";

      const img = document.createElement("img");
      img.className = "listing-card__image";
      img.src = item.image || "";
      img.alt = item.title;
      img.loading = "lazy";
      img.onerror = function () {
        this.style.display = "none";
      };

      const info = document.createElement("div");
      info.className = "listing-card__info";

      const title = document.createElement("div");
      title.className = "listing-card__title";
      title.textContent = item.title;

      const price = document.createElement("div");
      price.className = "listing-card__price";
      price.textContent = `$${item.price.toLocaleString()}`;

      info.append(title, price);
      a.append(img, info);
      grid.appendChild(a);
    }
  }

  function filterByPeriod(data, days) {
    if (days === 0 || data.length === 0) return data;
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
    const cutoffStr = cutoff.toISOString().split("T")[0];
    return data.filter((d) => d.date >= cutoffStr);
  }

  function initChart() {
    const filtered = filterByPeriod(historyData, activePeriod);
    renderChart(filtered);
  }

  function renderChart(data) {
    const canvas = document.getElementById("price-chart");
    const ctx = canvas.getContext("2d");

    if (chartInstance) {
      chartInstance.destroy();
    }

    if (data.length < 2) {
      document.getElementById("chart-empty").hidden = false;
      return;
    }
    document.getElementById("chart-empty").hidden = true;

    const labels = data.map((d) => d.date);
    const medians = data.map((d) => d.median);
    const q1s = data.map((d) => d.q1);
    const q3s = data.map((d) => d.q3);

    chartInstance = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Q3",
            data: q3s,
            borderColor: "transparent",
            backgroundColor: "rgba(29, 29, 31, 0.06)",
            fill: "+1",
            pointRadius: 0,
            tension: 0.3,
          },
          {
            label: "중앙값",
            data: medians,
            borderColor: "#1d1d1f",
            backgroundColor: "rgba(29, 29, 31, 0.1)",
            borderWidth: 2,
            pointRadius: data.length < 60 ? 3 : 0,
            pointHoverRadius: 5,
            tension: 0.3,
            fill: false,
          },
          {
            label: "Q1",
            data: q1s,
            borderColor: "transparent",
            backgroundColor: "rgba(29, 29, 31, 0.06)",
            fill: "-1",
            pointRadius: 0,
            tension: 0.3,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: "index",
          intersect: false,
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => items[0].label,
              label: (item) => {
                if (item.datasetIndex === 1) {
                  return `중앙값: $${item.parsed.y?.toLocaleString() ?? "-"}`;
                }
                return null;
              },
              afterBody: (items) => {
                const idx = items[0].dataIndex;
                const q1 = q1s[idx];
                const q3 = q3s[idx];
                return `Q1-Q3: $${q1?.toLocaleString() ?? "-"} - $${q3?.toLocaleString() ?? "-"}`;
              },
            },
            filter: (item) => item.datasetIndex === 1,
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: {
              maxTicksLimit: 8,
              font: { size: 11 },
            },
          },
          y: {
            grid: { color: "rgba(0,0,0,0.05)" },
            ticks: {
              callback: (v) => `$${v.toLocaleString()}`,
              font: { size: 11 },
            },
          },
        },
      },
    });
  }

  function initPeriodButtons() {
    const buttons = document.querySelectorAll(".period-btn");
    buttons.forEach((btn) => {
      btn.addEventListener("click", () => {
        buttons.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        activePeriod = parseInt(btn.dataset.period, 10);
        const filtered = filterByPeriod(historyData, activePeriod);
        renderChart(filtered);
      });
    });
  }

  init();
})();
