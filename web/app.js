let hypotheses = [];
let selectedId = null;

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

function statusClass(status) {
  return `status-${status}`;
}

function renderStats() {
  const total = hypotheses.length;
  const pass = hypotheses.filter((h) => h.status === "scan-passed" || h.status === "backtest-approved").length;
  const fail = hypotheses.filter((h) => h.status === "scan-failed").length;
  const other = total - pass - fail;
  document.getElementById("stat-total").textContent = total;
  document.getElementById("stat-pass").textContent = pass;
  document.getElementById("stat-fail").textContent = fail;
  document.getElementById("stat-other").textContent = other;
}

function renderTable() {
  const body = document.getElementById("hyp-table-body");
  body.innerHTML = "";
  for (const h of hypotheses) {
    const tr = document.createElement("tr");
    tr.className = "hyp-row" + (h.id === selectedId ? " selected" : "");
    tr.dataset.id = h.id;
    tr.innerHTML = `
      <td>${h.id}</td>
      <td>${h.family ?? ""}</td>
      <td>${h.title ?? ""}</td>
      <td><span class="status-badge ${statusClass(h.status)}">${h.status}</span></td>
      <td class="reason-tag">${h.decision_reasons.join(", ")}</td>
    `;
    tr.addEventListener("click", () => selectHypothesis(h.id));
    body.appendChild(tr);
  }
}

function switchTab(tab) {
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${tab}`));
}

function metricCard(label, value, cls = "") {
  return `<div class="metric-card"><div class="metric-label">${label}</div><div class="metric-value ${cls}">${value}</div></div>`;
}

function renderScanSummary(report) {
  const el = document.getElementById("scan-summary");
  if (!report) {
    el.innerHTML = `<div class="metric-card">No scan report yet</div>`;
    return;
  }
  const m = report.metrics;
  const tv = report.threshold_verdict;
  const cb = tv.cost_buffer;
  const cards = [
    metricCard("Status", tv.status, tv.status === "pass" ? "good" : tv.status === "fail" ? "bad" : ""),
    metricCard("IC (pooled)", m.ic_pooled?.toFixed(4) ?? "—"),
    metricCard("Hit rate", m.hit_rate ? (m.hit_rate * 100).toFixed(1) + "%" : "—"),
    metricCard("Raw N", m.raw_sample_count?.toLocaleString() ?? "—"),
    metricCard("Effective N", m.effective_sample_count ? Math.round(m.effective_sample_count).toLocaleString() : "—"),
    metricCard("Distinct days", report.data_window?.distinct_days ?? "—"),
  ];
  if (cb) {
    cards.push(
      metricCard("Edge (bps)", cb.edge_bps?.toFixed(3) ?? "—"),
      metricCard("Required (bps)", cb.required_bps?.toFixed(3) ?? "—", cb.cleared ? "good" : "bad"),
    );
  }
  const ctx = report.market_context;
  if (ctx) {
    cards.push(
      metricCard("Half-spread (bps)", ctx.avg_half_spread_bps?.toFixed(3) ?? "—"),
      metricCard("Realized vol (bps)", ctx.realized_volatility_bps?.toFixed(3) ?? "—"),
    );
  }
  el.innerHTML = cards.join("");
}

function renderLedger(events) {
  const el = document.getElementById("ledger-events");
  if (!events.length) {
    el.innerHTML = `<div class="metric-card">No ledger events yet</div>`;
    return;
  }
  el.innerHTML = events
    .map(
      (e) => `
      <div class="ledger-event">
        <div class="ledger-event-head"><span>${e.event_id}</span><span>${e.created_at}</span></div>
        <div class="ledger-transition">${e.from_status ?? "?"} &rarr; <strong>${e.to_status ?? "?"}</strong> (${e.actor_type}: ${e.actor_id})</div>
        ${e.decision_reason ? `<div class="ledger-reason">${e.decision_reason}</div>` : ""}
      </div>
    `
    )
    .reverse()
    .join("");
}

async function selectHypothesis(id) {
  selectedId = id;
  renderTable();
  document.getElementById("detail-empty").classList.add("hidden");
  document.getElementById("detail-content").classList.remove("hidden");

  const detail = await fetchJSON(`/api/hypotheses/${id}`);
  document.getElementById("detail-title").textContent = detail.summary.title || id;
  document.getElementById("detail-meta").textContent =
    `${id}  ·  ${detail.summary.family}  ·  ${detail.summary.status}` +
    (detail.summary.parent_id ? `  ·  variant of ${detail.summary.parent_id}` : "");

  document.getElementById("spec-json").textContent = JSON.stringify(detail.spec, null, 2);
  renderLedger(detail.events);

  const latestReportId = detail.summary.latest_scan_report_id;
  if (latestReportId) {
    const report = await fetchJSON(`/api/scan-reports/${latestReportId}`);
    renderScanSummary(report);
    document.getElementById("scan-json").textContent = JSON.stringify(report, null, 2);
  } else {
    renderScanSummary(null);
    document.getElementById("scan-json").textContent = "";
  }
}

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

async function init() {
  hypotheses = await fetchJSON("/api/hypotheses");
  renderStats();
  renderTable();
}

init();
