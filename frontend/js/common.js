/* Shared UI helpers: navigation, label maps, dropdown population. */

const PAGES = [
  { file: "index.html", label: "总览" },
  { file: "config.html", label: "场景配置" },
  { file: "individuals.html", label: "个体管理" },
  { file: "visualize.html", label: "实时可视化" },
  { file: "stats.html", label: "统计图表" },
  { file: "intervention.html", label: "干预措施" },
  { file: "alerts.html", label: "事件告警中心", alert: true },
  { file: "replay.html", label: "回放与时间轴" },
  { file: "compare.html", label: "对比实验" },
  { file: "report.html", label: "报告生成" },
  { file: "export.html", label: "数据导出" },
  { file: "history.html", label: "历史场景" },
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
      <a class="nav-link ${p.file === active ? "active" : ""}" href="/${p.file}"${p.alert ? ' data-alert-nav="1"' : ""}>${p.label}${p.alert ? '<span class="nav-alert-count" style="display:none" data-alert-count></span>' : ""}</a>
    `).join("")}`;
  refreshNavAlertCount();
}

/* Show a red count badge on the alert-center nav link when critical alerts
   have been recorded in the last 10 minutes (polled every 15 s). */
async function refreshNavAlertCount() {
  const badge = document.querySelector("[data-alert-count]");
  if (!badge) return;
  try {
    const { alerts } = await get("/api/alerts/latest?limit=100");
    const cutoff = Date.now() - 10 * 60 * 1000;
    const recent = alerts.filter((a) => a.severity === "critical" &&
      a.kind !== "recovered" &&
      new Date(a.time).getTime() > cutoff);
    if (recent.length) {
      badge.style.display = "inline-block";
      badge.textContent = recent.length > 99 ? "99+" : recent.length;
    } else {
      badge.style.display = "none";
    }
  } catch (e) { /* server may not be ready yet */ }
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

/* ---- real-time alert toasts -------------------------------------------- */
function toastStack() {
  let s = document.getElementById("toastStack");
  if (!s) {
    s = document.createElement("div");
    s.id = "toastStack";
    document.body.appendChild(s);
  }
  return s;
}

/* Poll newest alerts globally and toast anything fresh (all pages). */
let _lastAlertTime = "";
const _toastedIds = new Set();
function pushAlertToast(alert, runId) {
  if (!alert || !alert.id || _toastedIds.has(alert.id)) return;
  _toastedIds.add(alert.id);
  if (_toastedIds.size > 500) _toastedIds.clear();
  const cls = alert.kind === "recovered" ? "recovered" : (alert.severity || "info");
  const node = document.createElement("div");
  node.className = `toast ${cls}`;
  node.innerHTML = `<div>${esc(alert.message)}</div>
    <div class="tt">第 ${alert.step} 步 · ${esc(alert.time)}</div>`;
  node.onclick = () => {
    window.location.href = `/alerts.html?run=${encodeURIComponent(runId)}`;
  };
  toastStack().appendChild(node);
  setTimeout(() => node.remove(), 8000);
}

async function pollLatestAlerts() {
  try {
    const { alerts } = await get("/api/alerts/latest?limit=20");
    if (!alerts.length) return;
    if (!_lastAlertTime) {
      // First poll: establish a baseline without replaying history.
      _lastAlertTime = alerts[0].time;
      return;
    }
    const fresh = alerts
      .filter((a) => a.time > _lastAlertTime)
      .reverse();
    if (fresh.length) _lastAlertTime = fresh[fresh.length - 1].time;
    fresh.forEach((a) => pushAlertToast(a, a.run_id));
  } catch (e) { /* ignore polling errors */ }
}

/* Debounce helper for slider/input driven reloads. */
function debounce(fn, ms = 250) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

document.addEventListener("DOMContentLoaded", () => {
  renderNav();
  setInterval(refreshNavAlertCount, 15000);
  setInterval(pollLatestAlerts, 5000);
  pollLatestAlerts();
});
