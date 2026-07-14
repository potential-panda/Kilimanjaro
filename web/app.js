/* Kilimanjaro viewer — vanilla JS, no build step.
 * Views (hash-routed):
 *   #/scans                      list of grid-scan runs
 *   #/scans/{kind}/{id}          one run, sortable CSV table
 *   #/scans/{kind}/{id}/{file}   same, specific csv selected
 *   #/hyps                       hypotheses (spec + ledger join)
 *   #/hyps/{id}                  one hypothesis: spec + event history
 */

const $main = document.getElementById("main");

const fmt = {
  num(v, digits = 3) {
    const n = Number(v);
    if (!isFinite(n)) return v;
    if (Number.isInteger(n)) return String(n);
    return Math.abs(n) >= 1000 ? n.toFixed(0) : n.toFixed(digits);
  },
  date(ts) {
    return new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 16);
  },
};

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    e.append(c.nodeType ? c : document.createTextNode(c));
  }
  return e;
}

function verdictBadge(v) {
  if (!v) return null;
  return el("span", { class: `badge verdict-${v}` }, v);
}

/* ── sortable table ─────────────────────────────────────────────────────── */

const MAX_RENDER_ROWS = 2000;

function isNumericColumn(rows, ci) {
  let seen = 0;
  for (const row of rows) {
    const v = row[ci];
    if (v === "" || v == null) continue;
    if (isNaN(Number(v))) return false;
    if (++seen >= 50) break;
  }
  return seen > 0;
}

/* Renders a table with click-to-sort headers (asc ⇄ desc) and a text filter.
 * Numeric-aware; empty/NaN cells always sort last. */
function sortableTable(columns, rows) {
  const numeric = columns.map((_, ci) => isNumericColumn(rows, ci));
  let sortCol = null;
  let sortDir = -1; // first click on a column: descending (biggest first)
  let filter = "";

  const wrap = el("div", { class: "table-wrap" });
  const info = el("div", { class: "table-info" });
  const filterInput = el("input", {
    class: "table-filter",
    placeholder: "filter rows…",
    oninput: (e) => { filter = e.target.value.toLowerCase(); render(); },
  });
  const scroller = el("div", { class: "table-scroll" });
  wrap.append(el("div", { class: "table-bar" }, filterInput, info), scroller);

  function currentRows() {
    let out = rows;
    if (filter) {
      out = out.filter((r) => r.some((v) => String(v).toLowerCase().includes(filter)));
    }
    if (sortCol !== null) {
      const ci = sortCol, dir = sortDir, num = numeric[ci];
      out = [...out].sort((a, b) => {
        const av = a[ci], bv = b[ci];
        const aEmpty = av === "" || av == null, bEmpty = bv === "" || bv == null;
        if (aEmpty || bEmpty) return aEmpty && bEmpty ? 0 : aEmpty ? 1 : -1;
        if (num) {
          const an = Number(av), bn = Number(bv);
          const aNan = isNaN(an), bNan = isNaN(bn);
          if (aNan || bNan) return aNan && bNan ? 0 : aNan ? 1 : -1;
          return (an - bn) * dir;
        }
        return String(av).localeCompare(String(bv)) * dir;
      });
    }
    return out;
  }

  function render() {
    const data = currentRows();
    const shown = data.slice(0, MAX_RENDER_ROWS);
    info.textContent =
      `${data.length} rows` +
      (data.length !== rows.length ? ` (of ${rows.length})` : "") +
      (data.length > shown.length ? ` — showing first ${shown.length} after sort` : "");

    const thead = el("tr", {},
      ...columns.map((name, ci) =>
        el("th", {
          class: sortCol === ci ? "sorted" : "",
          onclick: () => {
            if (sortCol === ci) sortDir = -sortDir;
            else { sortCol = ci; sortDir = -1; }
            render();
          },
        }, name, sortCol === ci ? el("span", { class: "arrow" }, sortDir < 0 ? " ▼" : " ▲") : null)
      ));

    const vi = columns.indexOf("verdict");
    const body = shown.map((row) =>
      el("tr", { class: vi >= 0 && row[vi] === "stable" ? "row-stable" : "" },
        ...row.map((v, ci) => {
          if (ci === vi && v) return el("td", {}, verdictBadge(v));
          const cls = numeric[ci] ? (Number(v) < 0 ? "num neg" : "num") : "";
          return el("td", { class: cls }, numeric[ci] && v !== "" ? fmt.num(v) : v);
        })));

    scroller.replaceChildren(
      el("table", { class: "grid" }, el("thead", {}, thead), el("tbody", {}, ...body)));
  }

  render();
  return wrap;
}

/* ── scans list ─────────────────────────────────────────────────────────── */

async function viewScans() {
  $main.replaceChildren(el("div", { class: "loading" }, "loading scans…"));
  const scans = await getJSON("/api/scans");
  const rows = scans.map((s) =>
    el("tr", { class: "clickable", onclick: () => { location.hash = `#/scans/${s.kind}/${s.scan_id}`; } },
      el("td", {}, el("span", { class: `badge kind-${s.kind}` }, s.kind)),
      el("td", { class: "mono" }, s.scan_id),
      el("td", {}, s.market ?? ""),
      el("td", { class: "mono dim" },
        s.windows?.train ? `${s.windows.train[0]} .. ${s.windows.train[1]} | ${s.windows.holdout[0]} .. ${s.windows.holdout[1]}`
          : `${s.start ?? ""} .. ${s.end ?? ""}`),
      el("td", { class: "num" }, s.n_combos ?? ""),
      el("td", { class: "num" }, s.n_stable ?? ""),
      el("td", {}, ...Object.entries(s.verdicts || {})
        .filter(([v]) => v !== "noise" && v !== "no_data")
        .sort()
        .map(([v, n]) => el("span", { class: `badge verdict-${v}` }, `${v} ${n}`))),
      el("td", { class: "dim" }, fmt.date(s.mtime)),
    ));

  $main.replaceChildren(
    el("h1", {}, "Grid scans ", el("span", { class: "dim" }, `(${scans.length})`)),
    el("div", { class: "table-scroll" },
      el("table", { class: "grid" },
        el("thead", {}, el("tr", {},
          ...["kind", "scan id", "market", "train | holdout", "combos", "stable", "verdicts", "run at"]
            .map((h) => el("th", { class: "static" }, h)))),
        el("tbody", {}, ...rows))));
}

/* ── scan detail ────────────────────────────────────────────────────────── */

async function viewScanDetail(kind, scanId, file) {
  $main.replaceChildren(el("div", { class: "loading" }, "loading scan…"));
  const d = await getJSON(`/api/scans/${kind}/${scanId}`);
  const preferred = ["output.csv", "output_stable.csv", "output_monthly.csv"];
  const rank = (f) => { const i = preferred.indexOf(f); return i === -1 ? 99 : i; };
  const files = [...d.files].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  const active = file && files.includes(file) ? file : (files.includes("output.csv") ? "output.csv" : files[0]);

  const s = d.summary;
  const header = el("div", {},
    el("div", { class: "crumbs" },
      el("a", { href: "#/scans" }, "scans"), " / ",
      el("span", { class: `badge kind-${kind}` }, kind), " ",
      el("span", { class: "mono" }, scanId)),
    el("div", { class: "chips" },
      s.market ? el("span", { class: "chip" }, `market ${s.market}`) : null,
      s.windows?.train ? el("span", { class: "chip" }, `train ${s.windows.train[0]} .. ${s.windows.train[1]}`) : null,
      s.windows?.holdout ? el("span", { class: "chip" }, `holdout ${s.windows.holdout[0]} .. ${s.windows.holdout[1]}`) : null,
      s.n_combos != null ? el("span", { class: "chip" }, `${s.n_combos} combos`) : null,
      ...Object.entries(s.verdicts || {}).sort()
        .map(([v, n]) => el("span", { class: `badge verdict-${v}` }, `${v} ${n}`)),
      el("details", { class: "params" },
        el("summary", {}, "params.json"),
        el("pre", {}, JSON.stringify(d.params, null, 2)))));

  const tabs = el("div", { class: "tabs" },
    ...files.map((f) =>
      el("a", {
        class: f === active ? "tab active" : "tab",
        href: `#/scans/${kind}/${scanId}/${f}`,
      }, f)));

  $main.replaceChildren(header, tabs, el("div", { class: "loading" }, `loading ${active}…`));
  const csv = await getJSON(`/api/scans/${kind}/${scanId}/file/${active}`);
  $main.replaceChildren(header, tabs, sortableTable(csv.columns, csv.rows));
}

/* ── hypotheses ─────────────────────────────────────────────────────────── */

async function viewHyps() {
  $main.replaceChildren(el("div", { class: "loading" }, "loading hypotheses…"));
  const hyps = await getJSON("/api/hypotheses");
  const rows = hyps.map((h) =>
    el("tr", { class: "clickable", onclick: () => { location.hash = `#/hyps/${h.id}`; } },
      el("td", { class: "mono" }, h.id),
      el("td", {}, el("span", { class: `badge status-${h.status}` }, h.status)),
      el("td", {}, h.family ?? ""),
      el("td", { class: "title-cell" }, h.title ?? ""),
      el("td", { class: "num" }, h.event_count),
    ));
  $main.replaceChildren(
    el("h1", {}, "Hypotheses ", el("span", { class: "dim" }, `(${hyps.length})`)),
    el("div", { class: "table-scroll" },
      el("table", { class: "grid" },
        el("thead", {}, el("tr", {},
          ...["id", "status", "family", "title", "events"].map((h) => el("th", { class: "static" }, h)))),
        el("tbody", {}, ...rows))));
}

async function viewHypDetail(hypId) {
  $main.replaceChildren(el("div", { class: "loading" }, "loading…"));
  const d = await getJSON(`/api/hypotheses/${hypId}`);
  const s = d.summary;
  const events = [...d.events].reverse().map((e) =>
    el("div", { class: "event" },
      el("div", { class: "event-head" },
        el("span", { class: "mono dim" }, e.created_at),
        " ", el("span", { class: "badge" }, e.event_type),
        e.to_status ? el("span", { class: `badge status-${e.to_status}` }, `→ ${e.to_status}`) : null,
        e.decision_reason ? el("span", { class: "dim" }, ` ${e.decision_reason}`) : null),
      e.details?.headline ? el("div", { class: "event-headline" }, e.details.headline) : null,
      el("details", {}, el("summary", {}, "raw"), el("pre", {}, JSON.stringify(e, null, 2)))));

  $main.replaceChildren(
    el("div", { class: "crumbs" }, el("a", { href: "#/hyps" }, "hypotheses"), " / ",
      el("span", { class: "mono" }, hypId), " ",
      el("span", { class: `badge status-${s.status}` }, s.status)),
    el("h1", { class: "hyp-title" }, s.title ?? hypId),
    el("h2", {}, `Events (${d.events.length})`),
    el("div", {}, ...events),
    el("h2", {}, "Spec"),
    el("pre", { class: "spec" }, JSON.stringify(d.spec, null, 2)));
}

/* ── router ─────────────────────────────────────────────────────────────── */

async function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const view = parts[0] || "scans";
  for (const a of document.querySelectorAll("#nav a")) {
    a.classList.toggle("active", a.dataset.view === view);
  }
  try {
    if (view === "scans" && parts.length >= 3) await viewScanDetail(parts[1], parts[2], parts[3]);
    else if (view === "scans") await viewScans();
    else if (view === "hyps" && parts[1]) await viewHypDetail(parts[1]);
    else if (view === "hyps") await viewHyps();
    else location.hash = "#/scans";
  } catch (err) {
    $main.replaceChildren(el("div", { class: "error" }, String(err)));
  }
}

window.addEventListener("hashchange", route);
route();
