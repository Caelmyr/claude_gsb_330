/* Alert center: rules management + event list with filters and pagination. */

let rules = [];
let ruleMap = {};       // id -> rule
let metricLabels = {};  // domain -> [{key, label}]
let currentPage = 0;
const PAGE_SIZE = 50;
let currentFilters = {};

function severityBadge(severity) {
  const cls = { critical: "error", warning: "warn", info: "ready" }[severity] || "ready";
  const label = { critical: "严重", warning: "警告", info: "提示" }[severity] || severity;
  return `<span class="badge ${cls}">${label}</span>`;
}

function comparatorLabel(c) {
  return { gt: ">", ge: ">=", lt: "<", le: "<=" }[c] || c;
}

function metricLabel(domain, key) {
  const metrics = metricLabels[domain] || [];
  const m = metrics.find((x) => x.key === key);
  return m ? m.label : key;
}

// ---------------------------------------------------------------------------
// Rules management
// ---------------------------------------------------------------------------
async function loadRules() {
  const res = await get("/api/alerts/rules");
  rules = res.rules;
  ruleMap = Object.fromEntries(rules.map((r) => [r.id, r]));
  renderRules();
  updateRuleFilter();
}

function renderRules() {
  const list = el("ruleList");
  list.innerHTML = rules.map((r) => `
    <div class="list-item">
      <div style="min-width:0">
        <div class="t">${esc(r.name)} ${severityBadge(r.severity)}</div>
        <div class="s">${esc(metricLabel(r.domain, r.metric))} ${comparatorLabel(r.comparator)} ${fmt(r.threshold)} · ${r.repeat ? "重复提醒" : "仅一次"} · ${r.enabled ? "启用" : "禁用"}</div>
        ${r.description ? `<div class="s muted">${esc(r.description)}</div>` : ""}
      </div>
      <div class="row gap-6">
        <button class="btn small" data-rule="${r.id}" data-action="edit">编辑</button>
        <button class="btn small ${r.enabled ? "" : "success"}" data-rule="${r.id}" data-action="toggle">${r.enabled ? "禁用" : "启用"}</button>
        <button class="btn danger small" data-rule="${r.id}" data-action="delete">删除</button>
      </div>
    </div>`).join("") || '<p class="muted small">暂无规则。</p>';

  list.querySelectorAll("[data-action]").forEach((btn) => {
    btn.onclick = async () => {
      const ruleId = btn.dataset.rule;
      const action = btn.dataset.action;
      if (action === "edit") {
        editRule(ruleId);
      } else if (action === "toggle") {
        const rule = ruleMap[ruleId];
        await put(`/api/alerts/rules/${ruleId}`, { enabled: !rule.enabled });
        await loadRules();
      } else if (action === "delete") {
        if (!confirm("确认删除该规则？")) return;
        await del(`/api/alerts/rules/${ruleId}`);
        await loadRules();
      }
    };
  });
}

function editRule(ruleId) {
  const rule = ruleMap[ruleId];
  el("ruleForm").style.display = "block";
  el("ruleName").value = rule.name;
  el("ruleDomain").value = rule.domain || "";
  updateMetricSelect(rule.domain);
  el("ruleMetric").value = rule.metric;
  el("ruleComparator").value = rule.comparator;
  el("ruleThreshold").value = rule.threshold;
  el("ruleSeverity").value = rule.severity;
  el("ruleRepeat").checked = rule.repeat;
  el("ruleHysteresis").value = rule.hysteresis;
  el("ruleDescription").value = rule.description;
  el("btnSaveRule").dataset.ruleId = ruleId;
}

function newRule() {
  el("ruleForm").style.display = "block";
  el("ruleName").value = "";
  el("ruleDomain").value = "";
  updateMetricSelect("");
  el("ruleMetric").value = "";
  el("ruleComparator").value = "gt";
  el("ruleThreshold").value = 0;
  el("ruleSeverity").value = "warning";
  el("ruleRepeat").checked = true;
  el("ruleHysteresis").value = "";
  el("ruleDescription").value = "";
  el("btnSaveRule").dataset.ruleId = "";
}

function updateMetricSelect(domain) {
  const metrics = domain ? (metricLabels[domain] || []) : Object.values(metricLabels).flat();
  el("ruleMetric").innerHTML = metrics.map((m) => `<option value="${esc(m.key)}">${esc(m.label)}</option>`).join("");
}

async function saveRule() {
  const ruleId = el("btnSaveRule").dataset.ruleId;
  const rule = {
    name: el("ruleName").value,
    domain: el("ruleDomain").value,
    metric: el("ruleMetric").value,
    comparator: el("ruleComparator").value,
    threshold: parseFloat(el("ruleThreshold").value),
    severity: el("ruleSeverity").value,
    repeat: el("ruleRepeat").checked,
    hysteresis: el("ruleHysteresis").value ? parseFloat(el("ruleHysteresis").value) : null,
    description: el("ruleDescription").value,
  };
  try {
    if (ruleId) {
      await put(`/api/alerts/rules/${ruleId}`, rule);
    } else {
      await post("/api/alerts/rules", rule);
    }
    el("ruleForm").style.display = "none";
    await loadRules();
  } catch (e) {
    alert("保存失败：" + e.message);
  }
}

// ---------------------------------------------------------------------------
// Event list (with filters and pagination)
// ---------------------------------------------------------------------------
async function loadEvents(append = false) {
  const params = new URLSearchParams();
  if (currentFilters.runId) params.set("run_id", currentFilters.runId);
  if (currentFilters.ruleId) params.set("rule_id", currentFilters.ruleId);
  if (currentFilters.metric) params.set("metric", currentFilters.metric);
  if (currentFilters.severity) params.set("severity", currentFilters.severity);
  if (currentFilters.startStep) params.set("start_step", currentFilters.startStep);
  if (currentFilters.endStep) params.set("end_step", currentFilters.endStep);
  params.set("limit", PAGE_SIZE);
  params.set("offset", currentPage * PAGE_SIZE);

  const res = await get(`/api/alerts/events?${params}`);
  const events = res.events;
  const list = el("eventList");
  if (!append) {
    list.innerHTML = "";
    el("eventCount").textContent = `共 ${res.total} 条`;
  }
  events.forEach((e) => {
    const div = document.createElement("div");
    div.className = "list-item";
    div.innerHTML = `
      <div style="min-width:0">
        <div class="t">${esc(e.rule_name)} ${severityBadge(e.severity)}</div>
        <div class="s">${esc(e.run_name)} · 第 ${e.step} 步 · ${esc(e.time)}</div>
        <div class="s">${esc(e.message)}</div>
      </div>
      <div class="row gap-6">
        <a class="btn small" href="/visualize.html?run=${esc(e.run_id)}&step=${e.step}">查看</a>
      </div>`;
    list.appendChild(div);
  });
  el("btnLoadMore").style.display = res.has_more ? "block" : "none";
}

function updateRuleFilter() {
  const sel = el("filterRule");
  sel.innerHTML = '<option value="">全部规则</option>' +
    rules.map((r) => `<option value="${esc(r.id)}">${esc(r.name)}</option>`).join("");
}

async function updateRunFilter() {
  const res = await get("/api/runs");
  const sel = el("filterRun");
  sel.innerHTML = '<option value="">全部运行</option>' +
    res.runs.map((r) => `<option value="${esc(r.id)}">${esc(r.name)}</option>`).join("");
}

function updateMetricFilter() {
  const sel = el("filterMetric");
  const metrics = Object.values(metricLabels).flat();
  sel.innerHTML = '<option value="">全部指标</option>' +
    metrics.map((m) => `<option value="${esc(m.key)}">${esc(m.label)}</option>`).join("");
}

// ---------------------------------------------------------------------------
// Alert summary (top cards)
// ---------------------------------------------------------------------------
async function loadSummary() {
  const res = await get("/api/alerts/summary");
  el("alertStats").innerHTML = `
    <div class="stat"><div class="k">总事件数</div><div class="v">${res.total_events}</div></div>
    <div class="stat"><div class="k">严重</div><div class="v" style="color:var(--red)">${res.critical_count}</div></div>
    <div class="stat"><div class="k">警告</div><div class="v" style="color:var(--orange)">${res.warning_count}</div></div>
    <div class="stat"><div class="k">启用规则</div><div class="v">${res.active_rules}</div></div>`;
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
async function init() {
  // Load metric labels from catalog
  const catalog = await get("/api/catalog");
  for (const [domain, info] of Object.entries(catalog.domains)) {
    metricLabels[domain] = info.metrics;
  }

  // Domain select for rule form
  el("ruleDomain").innerHTML = '<option value="">全部领域</option>' +
    Object.entries(catalog.domains).map(([k, v]) => `<option value="${esc(k)}">${esc(v.label)}</option>`).join("");
  el("ruleDomain").onchange = () => updateMetricSelect(el("ruleDomain").value);

  await updateRunFilter();
  updateMetricFilter();
  await loadRules();
  await loadSummary();

  el("btnFilter").onclick = () => {
    currentFilters = {
      runId: el("filterRun").value,
      ruleId: el("filterRule").value,
      metric: el("filterMetric").value,
      severity: el("filterSeverity").value,
      startStep: el("filterStartStep").value ? parseInt(el("filterStartStep").value, 10) : null,
      endStep: el("filterEndStep").value ? parseInt(el("filterEndStep").value, 10) : null,
    };
    currentPage = 0;
    loadEvents();
  };
  el("btnReset").onclick = () => {
    el("filterRun").value = "";
    el("filterRule").value = "";
    el("filterMetric").value = "";
    el("filterSeverity").value = "";
    el("filterStartStep").value = "";
    el("filterEndStep").value = "";
    currentFilters = {};
    currentPage = 0;
    loadEvents();
  };
  el("btnLoadMore").onclick = () => {
    currentPage++;
    loadEvents(true);
  };
  el("btnNewRule").onclick = newRule;
  el("btnSaveRule").onclick = saveRule;
  el("btnCancelRule").onclick = () => { el("ruleForm").style.display = "none"; };

  // Load events initially
  await loadEvents();
}

init().catch((e) => console.error(e));
