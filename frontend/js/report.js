/* Report generation: summary + metrics table + config + events. */

async function generate() {
  const runId = el("runSelect").value;
  if (!runId) { alert("请选择运行"); return; }
  el("genStatus").textContent = "生成中…";
  try {
    const rpt = await post(`/api/reports/${runId}`);
    render(rpt);
    el("genStatus").textContent = "已生成 ✓";
  } catch (e) { el("genStatus").textContent = "生成失败：" + e.message; }
}

function render(rpt) {
  const metrics = Object.values(rpt.metrics || {});
  const metricRows = metrics.map((m) => `
    <tr><td>${esc(m.label)}</td><td class="num">${fmt(m.initial)}</td>
    <td class="num">${fmt(m.final)}</td><td class="num">${fmt(m.min)}</td>
    <td class="num">${fmt(m.max)}</td><td class="num">${m.peak_step}</td></tr>`).join("");

  const events = (rpt.events || []).slice().reverse().map((e) => `
    <tr><td>${e.step}</td><td>${esc(e.type)}</td><td class="muted small">${esc((e.result && e.result.reason) || "")}</td></tr>`).join("");

  const as = rpt.alert_summary || { total: 0, critical: 0, warning: 0, info: 0 };
  const KIND = { fire: "触发", repeat: "重复", recovered: "恢复", intervention: "干预" };
  const sevCls = (a) => a.kind === "recovered" ? "recovered" : (a.severity || "info");
  const alertRows = (rpt.alerts || []).slice().reverse().slice(0, 200).map((a) => `
    <tr>
      <td class="num mono">${a.step}</td>
      <td><span class="sev-dot ${sevCls(a)}"></span></td>
      <td>${esc(a.message)}</td>
      <td><span class="badge sev-${sevCls(a)}">${esc(KIND[a.kind] || a.kind)}</span></td>
      <td class="muted small">${esc(a.time || "")}</td>
    </tr>`).join("");

  el("reportBody").innerHTML = `
    <div class="card">
      <div class="row between">
        <h2 style="margin:0">${esc(rpt.name)}</h2>
        <div class="row gap-6">
          <span class="badge">${DOMAIN_LABEL[rpt.domain] || rpt.domain}</span>
          <span class="badge">${MODEL_LABEL[rpt.model] || rpt.model}</span>
          <span class="badge ready">${rpt.steps} 步</span>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>结论摘要</h2>
      <ul style="margin:0;padding-left:18px">${(rpt.summary || []).map((s) => `<li style="margin:6px 0">${esc(s)}</li>`).join("")}</ul>
    </div>

    <div class="card">
      <h2>指标统计</h2>
      <div style="overflow-x:auto"><table>
        <thead><tr><th>指标</th><th class="num">初始</th><th class="num">最终</th><th class="num">最小</th><th class="num">最大</th><th class="num">峰值步</th></tr></thead>
        <tbody>${metricRows || '<tr><td colspan="6" class="muted">无指标</td></tr>'}</tbody>
      </table></div>
    </div>

    <div class="card">
      <div class="row between">
        <h2 style="margin:0">事件告警（${as.total}）</h2>
        <div class="row gap-6">
          <span class="badge sev-critical">严重 ${as.critical || 0}</span>
          <span class="badge sev-warning">警告 ${as.warning || 0}</span>
          <span class="badge sev-info">提示 ${as.info || 0}</span>
          <a class="btn small" href="/alerts.html?run=${encodeURIComponent(rpt.run_id)}">告警中心筛选 →</a>
        </div>
      </div>
      <div style="overflow-x:auto;margin-top:10px"><table>
        <thead><tr><th>步</th><th></th><th>事件</th><th>类型</th><th>时间</th></tr></thead>
        <tbody>${alertRows || '<tr><td colspan="5" class="muted">无告警</td></tr>'}</tbody>
      </table></div>
      ${(rpt.alerts || []).length > 200 ? '<p class="muted small">仅展示最近 200 条，完整列表请在告警中心按条件筛选。</p>' : ""}
    </div>

    <div class="grid grid-2">
      <div class="card">
        <h2>干预日志（${(rpt.events || []).length}）</h2>
        <div style="overflow-x:auto"><table>
          <thead><tr><th>步</th><th>类型</th><th>结果</th></tr></thead>
          <tbody>${events || '<tr><td colspan="3" class="muted">无干预</td></tr>'}</tbody>
        </table></div>
      </div>
      <div class="card">
        <h2>配置参数</h2>
        <pre class="mono small" style="margin:0;white-space:pre-wrap;color:var(--accent-2)">${esc(JSON.stringify(rpt.config, null, 2))}</pre>
      </div>
    </div>`;
}

async function init() {
  el("genBtn").onclick = generate;
  await fillRunSelect(el("runSelect"));
  const q = new URLSearchParams(window.location.search).get("run");
  if (q && el("runSelect").querySelector(`option[value="${q}"]`)) {
    el("runSelect").value = q;
    await generate();
  } else if (el("runSelect").options.length > 1) {
    el("runSelect").selectedIndex = 1;
    await generate();
  }
}

init().catch((e) => console.error(e));
