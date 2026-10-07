/* Real-time visualization: step loop + canvas rendering + live alerts. */

let runId = null;
let meta = null;
let labels = {};
let running = false;
let timer = null;
let alertRows = [];
let seenAlertIds = new Set();

const SEV_LABEL = { critical: "严重", warning: "警告", info: "提示" };

function statusLine(t) { el("statusLine").textContent = t; }

function alertSevClass(a) {
  return a.kind === "recovered" ? "recovered" : (a.severity || "info");
}

function renderAlertFeed(newOnes) {
  if (newOnes && newOnes.length) {
    alertRows = [...newOnes, ...alertRows].slice(0, 50);
    newOnes.forEach((a) => {
      if (!seenAlertIds.has(a.id)) {
        seenAlertIds.add(a.id);
        pushAlertToast(a, runId);
      }
    });
  }
  if (!alertRows.length) {
    el("alertCard").style.display = "none";
    return;
  }
  el("alertCard").style.display = "block";
  el("alertFeed").innerHTML = alertRows.slice(0, 8).map((a) => {
    const sc = alertSevClass(a);
    const kind = { fire: "触发", repeat: "重复提醒", recovered: "恢复", intervention: "干预" }[a.kind] || a.kind;
    return `<div class="alert-row ${sc}">
      <span class="step-tag">第 ${a.step} 步</span>
      <div style="min-width:0;flex:1">
        <div class="msg"><span class="sev-dot ${sc}"></span>${esc(a.message)}</div>
        <div class="meta-line">${esc(a.time)} · <span class="badge sev-${sc}">${esc(kind)}</span></div>
      </div>
    </div>`;
  }).join("");
}

async function fetchRecentAlerts(announce) {
  if (!runId) return;
  try {
    const data = await get(`/api/runs/${runId}/alerts?page=1&page_size=50`);
    const rows = data.alerts || [];
    if (announce) {
      const fresh = rows.filter((a) => !seenAlertIds.has(a.id));
      if (fresh.length) {
        renderAlertFeed(fresh);
        return;
      }
    }
    alertRows = rows;
    rows.forEach((a) => seenAlertIds.add(a.id));
    renderAlertFeed(null);
  } catch (e) { /* ignore */ }
}

async function refreshRuns() {
  const runs = await fillRunSelect(el("runSelect"));
  if (runId && runs.some((r) => r.id === runId)) {
    el("runSelect").value = runId;
  } else if (runId) {
    runId = null;
  }
}

async function loadRun(id) {
  runId = id;
  seenAlertIds = new Set();
  alertRows = [];
  el("alertCenterLink").href = `/alerts.html?run=${encodeURIComponent(id)}`;
  meta = await get(`/api/runs/${id}`);
  await loadSnapshot();
  fetchRecentAlerts(false);
}

async function loadSnapshot() {
  if (!runId) return;
  const snap = await get(`/api/runs/${runId}/snapshot`);
  drawSnapshot(snap);
  const cur = await get(`/api/runs/${runId}`);
  statusLine(`第 ${cur.current_step} 步 · ${cur.status}`);
}

function drawSnapshot(snap) {
  renderSnapshot(el("canvas"), snap, meta.domain, meta.model);
  renderLegend(el("legend"), snap.palette);
  renderStats(snap.stats);
}

function renderStats(stats) {
  el("statTiles").innerHTML = Object.entries(stats).map(([k, v]) => `
    <div class="stat"><div class="k">${esc(labels[k] || k)}</div>
    <div class="v">${fmt(v)}</div></div>`).join("");
}

async function tick() {
  if (!running || !runId) return;
  try {
    const n = parseInt(el("stepsPerTick").value, 10) || 1;
    const r = await post(`/api/runs/${runId}/step`, { n });
    drawSnapshot(r.snapshot);
    statusLine(`第 ${r.step} 步 · 运行中`);
    if (r.alerts && r.alerts.length) {
      if (el("alertFollow").checked) renderAlertFeed(r.alerts);
      else r.alerts.forEach((a) => seenAlertIds.add(a.id));
    }
  } catch (e) {
    running = false;
    statusLine("已停止：" + e.message);
    return;
  }
  const delay = parseInt(el("speedSel").value, 10) || 160;
  timer = setTimeout(tick, delay);
}

function start() { if (!runId) { alert("请先选择运行"); return; } running = true; tick(); }
function pause() { running = false; if (timer) clearTimeout(timer); }

async function reset() {
  pause();
  if (!runId) return;
  await post(`/api/runs/${runId}/reset`);
  alertRows = [];
  seenAlertIds = new Set();
  renderAlertFeed(null);
  await loadSnapshot();
  statusLine("已重置到第 0 步");
}

async function init() {
  const { domains } = await get("/api/catalog");
  for (const d of Object.values(domains)) {
    for (const m of d.metrics) labels[m.key] = m.label;
  }
  el("runSelect").onchange = (e) => { if (e.target.value) loadRun(e.target.value); };
  el("refreshRuns").onclick = refreshRuns;
  el("btnStart").onclick = start;
  el("btnPause").onclick = pause;
  el("btnStep").onclick = async () => {
    if (!runId) return;
    const r = await post(`/api/runs/${runId}/step`, { n: 1 });
    drawSnapshot(r.snapshot);
    statusLine(`第 ${r.step} 步`);
    if (r.alerts && r.alerts.length) renderAlertFeed(r.alerts);
  };
  el("btnReset").onclick = reset;
  // Background batches / interventions produce alerts on the server; poll too.
  setInterval(() => { if (runId) fetchRecentAlerts(true); }, 5000);

  await refreshRuns();
  const q = new URLSearchParams(window.location.search).get("run");
  if (q && el("runSelect").querySelector(`option[value="${q}"]`)) {
    el("runSelect").value = q;
    await loadRun(q);
  }
}

init().catch((e) => statusLine("初始化失败：" + e.message));
