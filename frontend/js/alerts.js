/* Event alert center: rule management + filtered/paginated alert timeline. */

let runId = null;
let metrics = [];
let metricLabels = {};
let rules = [];
let page = 1;
let pageSize = 50;
let autoTimer = null;

const KIND_LABEL = { fire: "触发", repeat: "重复提醒", recovered: "恢复", intervention: "干预" };

function sevClass(a) {
  if (a.kind === "recovered") return "recovered";
  return a.severity || "info";
}

function sevDot(sev) {
  return `<span class="sev-dot ${sev}"></span>`;
}

async function refreshRuns(keepId) {
  const runs = await fillRunSelect(el("runSelect"));
  if (keepId && runs.some((r) => r.id === keepId)) el("runSelect").value = keepId;
  return runs;
}

function localToIso(v) {
  // datetime-local gives local time without zone; attach the local offset.
  if (!v) return "";
  const d = new Date(v);
  const off = -d.getTimezoneOffset();
  const sign = off >= 0 ? "+" : "-";
  const pad = (n) => String(Math.abs(Math.trunc(n))).padStart(2, "0");
  const hh = pad(off / 60), mm = pad(off % 60);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}${sign}${hh}:${mm}`;
}

function filters() {
  const f = new URLSearchParams();
  const kind = el("fKind").value;
  const sev = el("fSeverity").value;
  const metric = el("fMetric").value;
  const sf = el("fStepFrom").value;
  const st = el("fStepTo").value;
  const tf = localToIso(el("fTimeFrom").value);
  const tt = localToIso(el("fTimeTo").value);
  const q = el("fSearch").value.trim();
  if (kind) f.set("kind", kind);
  if (sev) f.set("severity", sev);
  if (metric) f.set("metric", metric);
  if (sf !== "") f.set("step_from", sf);
  if (st !== "") f.set("step_to", st);
  if (tf) f.set("time_from", tf);
  if (tt) f.set("time_to", tt);
  if (q) f.set("search", q);
  f.set("page", String(page));
  f.set("page_size", String(pageSize));
  return f;
}

async function loadRules() {
  const data = await get(`/api/runs/${runId}/alert-rules`);
  rules = data.rules;
  metrics = data.metrics || [];
  metricLabels = Object.fromEntries(metrics.map((m) => [m.key, m.label]));
  el("rMetric").innerHTML = metrics
    .map((m) => `<option value="${esc(m.key)}">${esc(m.label)}（${esc(m.key)}）</option>`).join("");
  el("fMetric").innerHTML = '<option value="">全部指标</option>' +
    metrics.map((m) => `<option value="${esc(m.key)}">${esc(m.label)}</option>`).join("");
  renderRules();
}

function renderRules() {
  el("ruleList").innerHTML = rules.map((r) => `
    <div class="rule-item ${r.enabled ? "" : "off"}" data-id="${esc(r.id)}">
      <div class="rule-head">
        <div style="min-width:0">
          <div class="t" style="font-size:13px">
            ${r.built_in ? "" : "<span class='pill'>自定义</span> "}
            ${esc(r.name)}
            <span class="badge sev-${r.severity}">${esc({critical:"严重",warning:"警告",info:"提示"}[r.severity] || r.severity)}</span>
          </div>
          <div class="rule-sub">
            ${esc(metricLabels[r.metric] || r.metric)} ${r.op === "gte" ? "≥" : "≤"} ${fmt(r.threshold)}
            · 连续 ${r.for_steps} 步
            · ${r.repeat ? `每 ${r.repeat_interval} 步重复提醒` : "同次越界仅一次"}
          </div>
        </div>
        <label class="switch" title="启用/停用">
          <input type="checkbox" class="rule-toggle" ${r.enabled ? "checked" : ""}>
          <span class="slider2"></span>
        </label>
      </div>
      <div class="row gap-6" style="margin-top:8px">
        <button class="btn small rule-edit">编辑</button>
        ${r.built_in ? "" : '<button class="btn danger small rule-del">删除</button>'}
      </div>
    </div>`).join("") || '<p class="muted small">暂无规则，点击「新建规则」。</p>';

  el("ruleList").querySelectorAll(".rule-item").forEach((item) => {
    const id = item.dataset.id;
    const rule = rules.find((r) => r.id === id);
    item.querySelector(".rule-toggle").onchange = async (e) => {
      await put(`/api/runs/${runId}/alert-rules/${id}`, { ...rule, enabled: e.target.checked });
      await loadRules();
    };
    item.querySelector(".rule-edit").onclick = () => openModal(rule);
    const delBtn = item.querySelector(".rule-del");
    if (delBtn) delBtn.onclick = async () => {
      if (!confirm("删除这条自定义规则？")) return;
      await del(`/api/runs/${runId}/alert-rules/${id}`);
      await loadRules();
    };
  });
}

function openModal(rule) {
  el("modalTitle").textContent = rule ? "编辑告警规则" : "新建告警规则";
  el("rId").value = rule ? rule.id : "";
  el("rName").value = rule ? rule.name : "";
  if (rule) el("rMetric").value = rule.metric;
  el("rSeverity").value = rule ? rule.severity : "warning";
  el("rOp").value = rule ? rule.op : "gte";
  el("rThreshold").value = rule ? rule.threshold : "";
  el("rForSteps").value = rule ? rule.for_steps : 1;
  el("rRepeatInterval").value = rule && rule.repeat ? rule.repeat_interval : 0;
  el("rEnabled").checked = rule ? rule.enabled : true;
  el("ruleModal").style.display = "flex";
}

function closeModal() { el("ruleModal").style.display = "none"; }

async function saveRule() {
  const id = el("rId").value;
  const body = {
    name: el("rName").value.trim(),
    metric: el("rMetric").value,
    severity: el("rSeverity").value,
    op: el("rOp").value,
    threshold: el("rThreshold").value,
    for_steps: Number(el("rForSteps").value) || 1,
    repeat: Number(el("rRepeatInterval").value) > 0,
    repeat_interval: Number(el("rRepeatInterval").value) || 50,
    enabled: el("rEnabled").checked,
  };
  try {
    if (id) await put(`/api/runs/${runId}/alert-rules/${id}`, body);
    else await post(`/api/runs/${runId}/alert-rules`, body);
  } catch (e) { alert("保存失败：" + e.message); return; }
  closeModal();
  await loadRules();
}

async function loadAlerts() {
  if (!runId) return;
  const data = await get(`/api/runs/${runId}/alerts?${filters().toString()}`);
  renderAlerts(data);
  renderPager(data);
  el("totalLine").textContent = `共 ${data.total} 条（本运行共 ${(data.counts && data.counts.total) || 0} 条）`;
  renderSummary(data.counts || {});
  maybeBanner(data.alerts);
}

function renderSummary(c) {
  el("summary").innerHTML = `
    <span class="badge sev-critical">严重 ${c.critical || 0}</span>
    <span class="badge sev-warning">警告 ${c.warning || 0}</span>
    <span class="badge sev-recovered">恢复 ${c.recovered || 0}</span>
    <span class="badge">干预 ${c.intervention || 0}</span>
    <span class="badge">触发/重复 ${c.fire || 0}</span>`;
}

function renderAlerts(data) {
  if (!data.alerts.length) {
    el("alertList").innerHTML = '<p class="muted" style="padding:18px 0">当前筛选条件下暂无事件记录。</p>';
    return;
  }
  el("alertList").innerHTML = data.alerts.map((a) => {
    const sc = sevClass(a);
    const kindLabel = KIND_LABEL[a.kind] || a.kind;
    return `
    <div class="alert-row ${sc}">
      <span class="step-tag">第 ${a.step} 步</span>
      <div style="min-width:0; flex:1">
        <div class="msg">${sevDot(sc)}${esc(a.message)}</div>
        <div class="meta-line">
          ${esc(a.time)} ·
          <span class="badge sev-${sc}">${esc(kindLabel)}</span>
          ${a.metric ? `· ${esc(metricLabels[a.metric] || a.metric)} = ${fmt(a.value)}` : ""}
        </div>
      </div>
      <a class="btn small" href="/replay.html?run=${esc(runId)}&step=${a.step}">定位回放</a>
    </div>`;
  }).join("");
}

function renderPager(data) {
  const pages = data.pages;
  let html = `<button class="btn small" id="pgPrev" ${page <= 1 ? "disabled" : ""}>‹ 上一页</button>
              <span class="muted small">第 ${page} / ${Math.max(1, pages)} 页</span>
              <button class="btn small" id="pgNext" ${page >= pages ? "disabled" : ""}>下一页 ›</button>`;
  el("pager").innerHTML = html;
  if (page > 1) el("pgPrev").onclick = () => { page -= 1; loadAlerts(); };
  if (page < pages) el("pgNext").onclick = () => { page += 1; loadAlerts(); };
}

function maybeBanner(alerts) {
  // Banner while the newest event is an unresolved critical breach.
  const newestCritical = alerts.find((a) => a.kind !== "recovered" && a.severity === "critical");
  const banner = el("liveBanner");
  if (newestCritical) {
    banner.classList.add("show");
    banner.innerHTML = `⚠ ${esc(newestCritical.message)}（第 ${newestCritical.step} 步触发）
      <a class="btn small" style="margin-left:auto" href="/visualize.html?run=${esc(runId)}">去查看</a>`;
  } else {
    banner.classList.remove("show");
  }
}

async function selectRun(id) {
  runId = id || null;
  el("gotoViz").href = runId ? `/visualize.html?run=${runId}` : "#";
  if (!runId) { el("ruleList").innerHTML = ""; el("alertList").innerHTML = ""; return; }
  page = 1;
  await loadRules();
  await loadAlerts();
}

function setAutoRefresh(on) {
  if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
  if (on) autoTimer = setInterval(() => { if (runId) loadAlerts(); }, 4000);
}

async function init() {
  el("runSelect").onchange = (e) => selectRun(e.target.value);
  el("refreshBtn").onclick = async () => {
    await refreshRuns(runId);
    if (runId) loadAlerts();
  };
  el("applyFilter").onclick = () => { page = 1; loadAlerts(); };
  el("clearFilter").onclick = () => {
    ["fKind", "fSeverity", "fMetric", "fStepFrom", "fStepTo",
     "fTimeFrom", "fTimeTo", "fSearch"].forEach((id) => { el(id).value = ""; });
    page = 1; loadAlerts();
  };
  el("pageSize").onchange = (e) => { pageSize = Number(e.target.value); page = 1; loadAlerts(); };
  el("autoRefresh").onchange = (e) => setAutoRefresh(e.target.checked);
  el("addRule").onclick = () => openModal(null);
  el("restoreRules").onclick = async () => {
    if (!confirm("恢复为该领域的默认规则？当前所有规则（含自定义）将被替换。")) return;
    await post(`/api/runs/${runId}/alert-rules/restore`, {});
    await loadRules();
  };
  el("rCancel").onclick = closeModal;
  el("rSave").onclick = saveRule;
  el("ruleModal").onclick = (e) => { if (e.target.id === "ruleModal") closeModal(); };

  await refreshRuns();
  const q = new URLSearchParams(window.location.search).get("run");
  if (q && el("runSelect").querySelector(`option[value="${q}"]`)) {
    el("runSelect").value = q;
  } else if (el("runSelect").options.length > 1) {
    el("runSelect").selectedIndex = 1;
  }
  await selectRun(el("runSelect").value);
}

init().catch((e) => console.error(e));
