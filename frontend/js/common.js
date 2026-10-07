/* Shared UI helpers: navigation, label maps, dropdown population,
   alert banner and toast notifications. */

const PAGES = [
  { file: "index.html", label: "总览" },
  { file: "config.html", label: "场景配置" },
  { file: "individuals.html", label: "个体管理" },
  { file: "visualize.html", label: "实时可视化" },
  { file: "stats.html", label: "统计图表" },
  { file: "intervention.html", label: "干预措施" },
  { file: "replay.html", label: "回放与时间轴" },
  { file: "compare.html", label: "对比实验" },
  { file: "report.html", label: "报告生成" },
  { file: "export.html", label: "数据导出" },
  { file: "history.html", label: "历史场景" },
  { file: "alerts.html", label: "告警中心" },
];

const DOMAIN_LABEL = { traffic: "交通", ecology: "生态", epidemic: "传染病" };
const MODEL_LABEL = { ca: "元胞自动机", abm: "智能体模型" };

function currentPage() {
  const parts = window.location.pathname.split("/");
  return parts[parts.length - 1] || "index.html";
}

function renderNav() {
  const nav = document.getElementById("topnav");
  if (!nav) return;
  const active = currentPage();
  nav.innerHTML = `
    <span class="brand"><span class="dot">◈</span> 复杂系统模拟仿真平台</span>
    ${PAGES.map((p) => `
      <a class="nav-link ${p.file === active ? "active" : ""}" href="/${p.file}">${p.label}</a>
    `).join("")}
    <span id="alertBadge" class="alert-badge" style="display:none"></span>`;
}

/* Populate a <select> with scenes; returns the selected value. */
async function fillSceneSelect(sel, { includePlaceholder = true } = {}) {
  const { scenes } = await get("/api/scenes");
  sel.innerHTML = (includePlaceholder ? '<option value="">— 选择场景 —</option>' : "") +
    scenes.map((s) => `<option value="${esc(s.id)}">${esc(s.name)} (${DOMAIN_LABEL[s.domain] || s.domain} · ${MODEL_LABEL[s.model] || s.model})</option>`).join("");
  return scenes;
}

/* Populate a <select> with runs, newest first. */
async function fillRunSelect(sel, { includePlaceholder = true } = {}) {
  const { runs } = await get("/api/runs");
  sel.innerHTML = (includePlaceholder ? '<option value="">— 选择运行 —</option>' : "") +
    runs.map((r) => `<option value="${esc(r.id)}">${esc(r.name)} · 第${r.current_step}步</option>`).join("");
  return runs;
}

function showNotice(el, html, kind = "") {
  if (!el) return;
  el.innerHTML = html;
  el.style.display = html ? "block" : "none";
  if (kind) el.className = `notice ${kind}`;
}

function el(id) { return document.getElementById(id); }

/* Debounce helper for slider/input driven reloads. */
function debounce(fn, ms = 250) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// ---------------------------------------------------------------------------
// Alert banner (recent critical alerts) + toast notifications
// ---------------------------------------------------------------------------
let alertBannerTimer = null;
let lastAlertCount = 0;
let lastAlertId = null;

function showToast(message, severity = "info") {
  const toast = document.createElement("div");
  toast.className = `toast toast-${severity}`;
  toast.innerHTML = `
    <div class="toast-content">${esc(message)}</div>
    <button class="toast-close" aria-label="关闭">&times;</button>`;
  document.body.appendChild(toast);
  toast.querySelector(".toast-close").onclick = () => toast.remove();
  setTimeout(() => { if (toast.parentNode) toast.remove(); }, 6000);
}

async function updateAlertBanner() {
  try {
    const summary = await get("/api/alerts/summary");
    const recentCritical = summary.recent_critical || 0;
    const banner = document.getElementById("alertBanner");
    const badge = document.getElementById("alertBadge");

    // Update the nav badge
    if (badge) {
      if (recentCritical > 0) {
        badge.textContent = recentCritical;
        badge.style.display = "inline-block";
        badge.className = "alert-badge critical";
      } else if (summary.warning_count > 0) {
        badge.textContent = summary.warning_count;
        badge.style.display = "inline-block";
        badge.className = "alert-badge warning";
      } else {
        badge.style.display = "none";
      }
    }

    // Update the banner (top of the container)
    if (banner) {
      if (recentCritical > 0) {
        const latest = summary.recent_events.find((e) => e.severity === "critical");
        banner.innerHTML = `
          <div class="alert-banner critical">
            <span class="alert-icon">⚠</span>
            <span class="alert-text">最近 5 分钟内有 ${recentCritical} 条严重告警${latest ? `：${esc(latest.message)}` : ""}</span>
            <a href="/alerts.html" class="alert-link">查看详情</a>
          </div>`;
      } else {
        banner.innerHTML = "";
      }
    }

    // Show toast for new critical alerts
    if (recentCritical > 0 && summary.recent_events.length > 0) {
      const latest = summary.recent_events[0];
      if (latest.severity === "critical" && latest.id !== lastAlertId) {
        showToast(latest.message, "critical");
        lastAlertId = latest.id;
      }
    }

    lastAlertCount = recentCritical;
  } catch (e) {
    // Ignore errors (e.g., server not ready)
  }
}

function startAlertBanner() {
  const container = document.querySelector(".container");
  if (!container) return;
  const banner = document.createElement("div");
  banner.id = "alertBanner";
  container.insertBefore(banner, container.firstChild);
  updateAlertBanner();
  alertBannerTimer = setInterval(updateAlertBanner, 5000);
}

document.addEventListener("DOMContentLoaded", () => {
  renderNav();
  startAlertBanner();
});
