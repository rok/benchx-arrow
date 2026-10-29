// benchx-arrow dashboard: a static page over the JSON views chDB writes in the
// publish job (tools/views.py). No query engine runs here; every number shown
// was computed there. Routes: #/ landing, #/s/<suite>, #/b/<suite>/<case>?params=&q=&node=,
// #/c/<sha>. Data is fetched with cache-busting because the Pages CDN caches for hours.

const DATA = "data/";
const GITHUB = "https://github.com/apache/arrow/commit/";
const SERIES_VARS = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6", "--series-7", "--series-8"];
const state = { v: "", manifest: null, landing: null, series: null, sparks: null, histories: new Map(), commits: new Map(), chart: null, nodeSlots: new Map() };

// ---- utilities ------------------------------------------------------------------

function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;            // only for trusted markup written in this file
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

async function getJSON(path, { fresh = false } = {}) {
  const url = DATA + path + (fresh ? `?t=${Date.now()}` : `?v=${encodeURIComponent(state.v)}`);
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

const UNITS = {
  s: [["ns", 1e-9], ["µs", 1e-6], ["ms", 1e-3], ["s", 1]],
  B: [["B", 1], ["KiB", 1024], ["MiB", 1024 ** 2], ["GiB", 1024 ** 3]],
};
function pickScale(values, unit) {
  const table = UNITS[unit];
  const m = Math.max(...values.filter((v) => v != null && isFinite(v)).map(Math.abs), 0);
  if (!table || !m) return [unit, 1];
  let best = table[0];
  for (const t of table) if (m >= t[1]) best = t;
  return best;
}
function fmtNum(v, digits = 3) {
  if (v == null || !isFinite(v)) return "–";
  const a = Math.abs(v);
  if (a >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (a >= 100) return v.toFixed(digits - 3 < 0 ? 0 : digits - 3);
  if (a >= 10) return v.toFixed(digits - 2);
  return v.toPrecision(digits);
}
function fmtValue(v, unit, scale) {
  if (v == null) return "–";
  const [label, factor] = scale || pickScale([v], unit);
  return `${fmtNum(v / factor)} ${label}`;
}
function fmtPct(x, digits = 1) {
  if (x == null || !isFinite(x)) return "–";
  const s = (x * 100).toFixed(digits);
  return (x > 0 ? "+" : "") + s + "%";
}
function fmtAbsPct(x, digits = 1) { return x == null || !isFinite(x) ? "–" : (Math.abs(x) * 100).toFixed(digits) + "%"; }
function parseDate(s) {
  if (!s) return null;
  const iso = s.includes("T") ? s : s.replace(" ", "T") + (s.endsWith("Z") || /[+-]\d\d:\d\d$/.test(s) ? "" : "Z");
  const d = new Date(iso);
  return isNaN(d) ? null : d;
}
function fmtDate(s) {
  const d = parseDate(s);
  return d ? d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "–";
}
function ago(s) {
  const d = parseDate(s);
  if (!d) return "";
  const m = Math.round((Date.now() - d) / 60000);
  if (m < 60) return `${m} min ago`;
  if (m < 48 * 60) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} d ago`;
}
function verdict(rel, direction, flatBelow = 0.005) {
  if (rel == null || !isFinite(rel)) return "none";
  if (Math.abs(rel) < flatBelow) return "flat";
  const worse = direction === "higher-is-better" ? rel < 0 : rel > 0;
  return worse ? "regressed" : "improved";
}
function delta(rel, direction, opts = {}) {
  const v = verdict(rel, direction);
  const glyph = v === "regressed" ? "▲" : v === "improved" ? "▼" : v === "flat" ? "■" : "";
  const words = { regressed: "regressed", improved: "improved", flat: "no change", none: "no comparison" }[v];
  return h("span", { class: `delta num ${v}`, title: words, "aria-label": `${fmtPct(rel)} ${words}` },
    glyph && h("span", { class: "glyph", "aria-hidden": "true" }, glyph), fmtPct(rel, opts.digits ?? 1));
}
function benchHref(s) {
  const q = new URLSearchParams();
  if (s.params) q.set("params", s.params);
  if (s.quantity && s.quantity !== "wall-time") q.set("q", s.quantity);
  const qs = q.toString();
  return `#/b/${encodeURIComponent(s.suite)}/${encodeURIComponent(s.case)}${qs ? "?" + qs : ""}`;
}
function caseLabel(s) { return s.params ? `${s.case} ${s.params}` : s.case; }
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function nodeColor(node) {
  if (!state.nodeSlots.has(node)) state.nodeSlots.set(node, state.nodeSlots.size % SERIES_VARS.length);
  return cssVar(SERIES_VARS[state.nodeSlots.get(node)]);
}
function table(headers, rows, opts = {}) {
  return h("div", { class: "wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, headers.map((x) => h("th", { class: x.startsWith(">") ? "r" : "" }, x.replace(/^>/, ""))))),
    h("tbody", {}, rows.length ? rows.map((cells) => h("tr", {}, cells.map((c, i) => h("td", { class: headers[i].startsWith(">") ? "r num" : "" }, c))))
      : h("tr", {}, h("td", { colspan: headers.length, class: "empty" }, opts.empty || "nothing yet")))));
}
function tile(label, value, hint, small) {
  return h("div", { class: "tile" }, h("div", { class: "label" }, label), h("div", { class: `value ${small ? "small" : ""}` }, value), hint && h("div", { class: "hint" }, hint));
}
function gapTag(ch) {
  if (ch.prev_is_parent) return null;
  return h("span", { class: "tag gap", title: `the previous measured commit is ${ch.gap} commits back, not the parent` }, `gap ${ch.gap}`);
}

// ---- views ----------------------------------------------------------------------

async function renderLanding() {
  const L = state.landing, st = L.stats, latest = st.latest;
  if (!state.sparks) state.sparks = await getJSON("views/sparks.json");
  const S = state.sparks;
  // One card per benchmark (case, params, quantity); its nodes are lines on the same chart.
  const groups = new Map();
  for (const s of S.series) {
    const key = `${s.suite}|${s.case}|${s.params}|${s.quantity}`;
    if (!groups.has(key)) groups.set(key, { suite: s.suite, case: s.case, params: s.params, quantity: s.quantity, unit: s.unit, direction: s.direction, list: [] });
    groups.get(key).list.push(s);
  }
  const all = [...groups.values()].map((g) => {
    const newest = g.list.filter((s) => s.latest != null).sort((a, b) => b.orders[b.orders.length - 1] - a.orders[a.orders.length - 1])[0];
    return { ...g, newest, change: newest?.vs_prev ?? null, noise: newest?.noise ?? null, v: newest ? verdict(newest.vs_prev, g.direction) : "none" };
  });
  const suites = [...new Set(all.map((g) => g.suite))].sort();
  const nodes = [...new Set(S.series.map((s) => s.node))].sort();
  const q = new URLSearchParams((location.hash.split("?")[1]) || "");
  const ctl = { suite: q.get("suite") || "all", sort: q.get("sort") || "change", text: q.get("f") || "", n: 48 };
  const setHash = () => { const p = new URLSearchParams(); if (ctl.suite !== "all") p.set("suite", ctl.suite); if (ctl.sort !== "change") p.set("sort", ctl.sort); if (ctl.text) p.set("f", ctl.text); history.replaceState(null, "", "#/" + (p.toString() ? "?" + p : "")); };
  const sorters = {
    change: (a, b) => Math.abs(b.change ?? -1) - Math.abs(a.change ?? -1),
    regressed: (a, b) => (b.v === "regressed") - (a.v === "regressed") || Math.abs(b.change ?? -1) - Math.abs(a.change ?? -1),
    improved: (a, b) => (b.v === "improved") - (a.v === "improved") || Math.abs(b.change ?? -1) - Math.abs(a.change ?? -1),
    noise: (a, b) => (b.noise ?? -1) - (a.noise ?? -1),
    name: (a, b) => a.suite.localeCompare(b.suite) || a.case.localeCompare(b.case) || a.params.localeCompare(b.params),
  };
  const grid = h("div", { class: "grid" });
  const status = h("span", { class: "note" });
  const more = h("button", { class: "ghost", onclick: () => { ctl.n += 48; draw(); } }, "show 48 more");
  const draw = () => {
    const needle = ctl.text.trim().toLowerCase();
    const rows = all.filter((g) => (ctl.suite === "all" || g.suite === ctl.suite) && (!needle || `${g.suite} ${g.case} ${g.params}`.toLowerCase().includes(needle))).sort(sorters[ctl.sort] || sorters.change);
    grid.replaceChildren();
    const shown = rows.slice(0, ctl.n), cards = [];
    for (const g of shown) {
      const plot = h("div", { class: "spark" });
      grid.append(h("a", { class: "mini", href: benchHref(g) },
        h("div", { class: "mini-head" }, h("span", { class: "mono name", title: caseLabel(g) }, caseLabel(g)), g.newest ? delta(g.change, g.direction) : null),
        h("div", { class: "mini-value num" }, g.newest ? fmtValue(g.newest.latest, g.unit) : "–", h("span", { class: "note" }, ` ${g.suite.replace(/^arrow-|-benchmark$/g, "")}`)),
        plot));
      cards.push([g, plot]);
    }
    status.textContent = `${shown.length} of ${rows.length} benchmarks`;
    more.hidden = shown.length >= rows.length;
    requestAnimationFrame(() => { for (const [g, plot] of cards) drawSpark(plot, g.list, S.axis); });
    setHash();
  };
  const select = (label, key, values, fmt = (x) => x) => h("label", {}, label, h("select", { onchange: (e) => { ctl[key] = e.target.value; ctl.n = 48; draw(); } }, values.map((v) => h("option", { value: v, selected: v === ctl[key] }, fmt(v)))));
  const filter = h("input", { type: "search", placeholder: "filter by name", value: ctl.text, "aria-label": "filter benchmarks", oninput: (e) => { ctl.text = e.target.value; ctl.n = 48; draw(); } });
  const main = h("main", {},
    h("div", { class: "tiles" },
      tile("commits measured", st.commits),
      tile("benchmark series", st.series),
      tile("results", st.results.toLocaleString()),
      tile("nodes", st.nodes.join(", ") || "–", null, true),
      latest ? tile("last measured", h("a", { href: `#/c/${latest.sha}` }, latest.short), `${ago(latest.finished_at || latest.committer_date)} · ${latest.node}`, true) : tile("last measured", "–")),
    h("div", { class: "filters", style: "margin-top:16px" },
      select("suite", "suite", ["all", ...suites], (v) => (v === "all" ? "all suites" : v)),
      select("sort", "sort", ["change", "regressed", "improved", "noise", "name"], (v) => ({ change: "largest change", regressed: "regressed first", improved: "improved first", noise: "noisiest", name: "name" })[v]),
      filter, status,
      nodes.length > 1 ? h("span", {}, nodes.map((n) => h("span", { class: "legend-key" }, h("span", { class: "key", style: `border-color:${nodeColor(n)}` }), n))) : null),
    h("p", { class: "note" }, `Each chart is the benchmark's last ${S.axis.length} measured commits; the change is against the previous measured point and is a heuristic until history-mode verdicts exist.`),
    grid,
    h("div", { style: "margin:12px 0 4px" }, more),
    h("h2", {}, "Recent commits"),
    table(["commit", "subject", "date", "node", ">suites", ">results", `>regressed >${L.notable * 100}%`, `>improved >${L.notable * 100}%`],
      L.recent_commits.map((c) => [
        h("a", { href: `#/c/${c.sha}`, class: "mono" }, c.short),
        h("span", {}, c.subject.length > 90 ? c.subject.slice(0, 88) + "…" : c.subject, " ", h("a", { href: GITHUB + c.sha, target: "_blank", rel: "noopener", class: "note" }, "github")),
        fmtDate(c.committer_date), c.node,
        h("span", {}, String(c.suites_ok), c.suites_failed ? h("span", { class: "tag failed", style: "margin-left:6px" }, `${c.suites_failed} failed`) : null),
        c.results, c.compared ? c.regressed : "–", c.compared ? c.improved : "–"])),
    h("div", { class: "cols", style: "margin-top:24px" },
      h("div", { class: "card" }, h("h3", {}, "Noisiest series"),
        h("p", { class: "note" }, "Median absolute change between consecutive points over the last 21. Changes on these need more evidence."),
        table(["benchmark", "node", ">noise", ">points"], L.noisiest.map((s) => [h("a", { href: benchHref(s) }, caseLabel(s)), s.node, fmtAbsPct(s.noise), s.points]), { empty: "needs at least three points per series" })),
      h("div", { class: "card" }, h("h3", {}, "Suite wall time on the latest commit"),
        table(["suite", "node", ">cases", ">failed", ">time"], L.suite_time.map((s) => [h("a", { href: `#/s/${encodeURIComponent(s.suite)}` }, s.suite), s.node, s.cases, s.failed || "0", `${Math.round(s.seconds / 60)} min`])))),
    footer());
  draw();
  return main;
}

async function renderSuite(suite) {
  const rows = state.series.filter((s) => s.suite === suite);
  const nodes = [...new Set(rows.map((r) => r.node))].sort();
  let hist = state.histories.get(suite);
  if (!hist) { hist = await getJSON(`views/history/${encodeURIComponent(suite)}.json`); state.histories.set(suite, hist); }
  // asv-style summary: one small history per benchmark (all nodes), newest value and change beside it.
  const groups = new Map();
  for (const s of Object.values(hist.series)) {
    const key = `${s.case}|${s.params}|${s.quantity}`;
    if (!groups.has(key)) groups.set(key, { case: s.case, params: s.params, quantity: s.quantity, unit: s.unit, direction: s.direction, suite, list: [] });
    groups.get(key).list.push(s);
  }
  const latestBy = new Map(rows.map((r) => [`${r.case}|${r.params}|${r.quantity}|${r.node}`, r]));
  const grid = h("div", { class: "grid" });
  const cards = [];
  for (const g of [...groups.values()].sort((a, b) => a.case.localeCompare(b.case) || a.params.localeCompare(b.params))) {
    const newest = g.list.map((s) => latestBy.get(`${s.case}|${s.params}|${s.quantity}|${s.node}`)).filter(Boolean).sort((a, b) => b.commit_order - a.commit_order)[0];
    const plot = h("div", { class: "spark" });
    const card = h("a", { class: "mini", href: benchHref(g) },
      h("div", { class: "mini-head" }, h("span", { class: "mono name" }, caseLabel(g)), newest ? delta(newest.vs_prev, g.direction) : null),
      h("div", { class: "mini-value num" }, newest ? fmtValue(newest.estimate, g.unit) : "–", h("span", { class: "note" }, newest && g.list.length > 1 ? ` ${newest.node}` : "")),
      plot);
    cards.push([g, plot]);
    grid.append(card);
  }
  const main = h("main", {},
    h("div", { class: "crumbs" }, h("a", { href: "#/" }, "home"), " / suite"),
    h("h2", {}, suite),
    h("p", { class: "lead" }, `${groups.size} benchmarks, ${rows.length} series on ${nodes.join(", ")}. Each chart is the benchmark's history over the ${hist.commits.length} measured commits; the change is against the previous measured point.`),
    nodes.length > 1 ? h("div", { class: "filters" }, h("span", { class: "note" }, "nodes: "), nodes.map((n) => h("span", { class: "legend-key" }, h("span", { class: "key", style: `border-color:${nodeColor(n)}` }), n))) : null,
    grid,
    h("details", { class: "table-view" }, h("summary", {}, "Table view"),
      table(["benchmark", "quantity", "node", ">latest", ">vs previous", ">vs 10 back", ">noise", ">points"],
        rows.map((s) => [h("a", { href: benchHref(s) }, caseLabel(s)), s.quantity, s.node, fmtValue(s.estimate, s.unit), delta(s.vs_prev, s.direction), delta(s.vs_10, s.direction), fmtAbsPct(s.noise), s.points]))),
    footer());
  requestAnimationFrame(() => { for (const [g, plot] of cards) drawSpark(plot, g.list, hist.commits); });
  return main;
}

function drawSpark(el, seriesList, axis) {
  const index = new Map(axis.map((c, i) => [c.commit_order, i]));
  const xs = axis.map((_, i) => i);
  const ys = seriesList.map((s) => { const col = new Array(xs.length).fill(null); s.orders.forEach((o, k) => { if (s.estimates[k] != null) col[index.get(o)] = s.estimates[k]; }); return col; });
  const vals = ys.flat().filter((v) => v != null);
  if (!vals.length) {
    const failed = seriesList.some((s) => s.statuses.some((st) => st && st !== "success"));
    el.append(h("div", { class: "note" }, failed ? h("span", { class: "tag failed" }, "failed on every commit") : "no data"));
    return;
  }
  const lo = Math.min(...vals), hi = Math.max(...vals), pad = (hi - lo) * 0.15 || hi * 0.05 || 1;
  const width = Math.max(120, el.clientWidth || 240);
  new uPlot({
    width, height: 56, legend: { show: false }, cursor: { show: false },
    scales: { x: { time: false, range: [-0.5, xs.length - 0.5] }, y: { range: [lo - pad, hi + pad] } },
    axes: [{ show: false }, { show: false }],
    series: [{}, ...seriesList.map((s) => ({ stroke: nodeColor(s.node), width: 1.5, spanGaps: true, points: { show: xs.length <= 12, size: 6, stroke: nodeColor(s.node), fill: cssVar("--surface"), width: 1.5 } }))],
  }, [xs, ...ys], el);
}

async function renderBench(suite, kase, query) {
  const params = query.get("params") || "";
  const quantity = query.get("q") || "wall-time";
  const nodeFilter = query.get("node") || "all";
  let hist = state.histories.get(suite);
  if (!hist) { hist = await getJSON(`views/history/${encodeURIComponent(suite)}.json`); state.histories.set(suite, hist); }
  const all = Object.values(hist.series).filter((s) => s.case === kase);
  const variants = [...new Set(all.map((s) => s.params))];
  const quantities = [...new Set(all.map((s) => s.quantity))];
  const nodes = [...new Set(all.map((s) => s.node))].sort();
  const shown = all.filter((s) => s.params === params && s.quantity === quantity && (nodeFilter === "all" || s.node === nodeFilter));
  const latestRows = state.series.filter((s) => s.suite === suite && s.case === kase && s.params === params && s.quantity === quantity && (nodeFilter === "all" || s.node === nodeFilter));
  const kpiNode = latestRows.sort((a, b) => b.commit_order - a.commit_order)[0];
  const unit = shown[0]?.unit || kpiNode?.unit || "";
  const direction = shown[0]?.direction || kpiNode?.direction || "";

  const setParam = (k, v) => { const q = new URLSearchParams(query); if (v == null || v === "" || v === "all" || (k === "q" && v === "wall-time")) q.delete(k); else q.set(k, v); location.hash = `#/b/${encodeURIComponent(suite)}/${encodeURIComponent(kase)}${q.toString() ? "?" + q : ""}`; };
  const select = (label, key, values, current, fmt = (x) => x) => h("label", {}, label, h("select", { onchange: (e) => setParam(key, e.target.value) }, values.map((v) => h("option", { value: v, selected: v === current }, fmt(v)))));

  const main = h("main", {},
    h("div", { class: "crumbs" }, h("a", { href: "#/" }, "home"), " / ", h("a", { href: `#/s/${encodeURIComponent(suite)}` }, suite)),
    h("h2", { class: "mono" }, kase, params ? ` ${params}` : ""),
    h("div", { class: "filters" },
      variants.length > 1 ? select("variant", "params", variants, params, (v) => v || "(default)") : null,
      quantities.length > 1 ? select("quantity", "q", quantities, quantity) : null,
      nodes.length > 1 ? select("node", "node", ["all", ...nodes], nodeFilter) : null),
    kpiNode ? h("div", { class: "tiles" },
      tile(`latest · ${kpiNode.node}`, fmtValue(kpiNode.estimate, unit), `${kpiNode.quantity}, commit ${kpiNode.sha.slice(0, 12)}`),
      tile("vs previous", delta(kpiNode.vs_prev, direction)),
      tile("vs 10 commits back", delta(kpiNode.vs_10, direction)),
      tile("vs 100 commits back", delta(kpiNode.vs_100, direction)),
      tile("noise", fmtAbsPct(kpiNode.noise), kpiNode.noise_rank != null && kpiNode.points >= 3 ? `quieter than ${Math.round((1 - kpiNode.noise_rank) * 100)}% of the suite` : "needs 3+ points"),
      tile("points", kpiNode.points, kpiNode.first_order != null ? `since measured commit #${kpiNode.first_order}` : null)) : h("p", { class: "empty" }, "no successful measurement yet"),
    h("h3", {}, "History"),
    h("p", { class: "note" }, "One point per measured commit, in commit order. Hover for the commit. ", direction ? `${direction.replace(/-/g, " ")}.` : ""));
  const chartWrap = h("div", { class: "chart-wrap" });
  main.append(chartWrap);
  const moves = biggestMoves(shown, hist.commits);
  main.append(h("h3", {}, "Biggest moves in this series"),
    table(["commit", "subject", "node", ">from", ">to", ">change"], moves.slice(0, 12).map((m) => [
      h("a", { href: `#/c/${m.sha}`, class: "mono" }, m.short), m.subject.length > 70 ? m.subject.slice(0, 68) + "…" : m.subject, m.node,
      fmtValue(m.from, unit), fmtValue(m.to, unit), delta(m.rel, direction)]), { empty: "needs two points" }),
    footer());
  requestAnimationFrame(() => drawHistory(chartWrap, shown, hist.commits, unit, kase));
  return main;
}

function biggestMoves(seriesList, axis) {
  const bySha = new Map(axis.map((c) => [c.commit_order, c]));
  const out = [];
  for (const s of seriesList) {
    for (let i = 1; i < s.orders.length; i++) {
      const a = s.estimates[i - 1], b = s.estimates[i];
      if (a == null || b == null || !(a > 0)) continue;
      const c = bySha.get(s.orders[i]);
      out.push({ sha: c?.sha || "", short: c?.short || "", subject: c?.subject || "", node: s.node, from: a, to: b, rel: b / a - 1 });
    }
  }
  return out.sort((x, y) => Math.abs(y.rel) - Math.abs(x.rel));
}

function drawHistory(wrap, seriesList, axis, unit, title) {
  wrap.replaceChildren();
  if (state.chart) { state.chart.destroy(); state.chart = null; }
  if (!seriesList.length || !axis.length) { wrap.append(h("div", { class: "empty" }, "no points to draw")); return; }
  const index = new Map(axis.map((c, i) => [c.commit_order, i]));
  const xs = axis.map((_, i) => i);
  const allValues = seriesList.flatMap((s) => s.estimates);
  const scale = pickScale(allValues, unit);
  const ys = seriesList.map((s) => { const col = new Array(xs.length).fill(null); s.orders.forEach((o, k) => { if (s.estimates[k] != null) col[index.get(o)] = s.estimates[k] / scale[1]; }); return col; });
  const totalPoints = allValues.filter((v) => v != null).length;
  const tip = h("div", { class: "tip" });
  const titleRow = h("div", { class: "chart-title" }, h("span", {}, `${title} · ${scale[0]}`), h("span", { class: "note" }, `${axis.length} commits`));
  const el = h("div", { class: "chart" });
  wrap.append(titleRow, el, tip);
  const width = Math.max(320, wrap.clientWidth - 16);
  const opts = {
    width, height: 300,
    legend: { show: seriesList.length > 1, live: false },
    cursor: { points: { size: 10 }, drag: { x: true, y: false } },
    scales: { x: { time: false, range: [-0.5, xs.length - 0.5] }, y: { range: (u, min, max) => { const pad = (max - min) * 0.25 || max * 0.1 || 1; return [Math.max(0, min - pad), max + pad]; } } },
    axes: [
      { stroke: cssVar("--muted"), grid: { show: false }, ticks: { stroke: cssVar("--axis"), width: 1 }, font: "12px system-ui, sans-serif", space: 70,
        splits: (u, axisIdx, min, max, incr) => { const step = Math.max(1, Math.ceil(incr)); const out = []; for (let i = Math.max(0, Math.ceil(min)); i <= Math.min(xs.length - 1, Math.floor(max)); i += step) out.push(i); return out; },
        values: (u, vals) => vals.map((v) => axis[v] ? axis[v].short.slice(0, 7) : "") },
      { stroke: cssVar("--muted"), grid: { stroke: cssVar("--grid"), width: 1 }, ticks: { show: false }, font: "12px system-ui, sans-serif", size: 60,
        values: (u, vals) => vals.map((v) => fmtNum(v, 3)) },
    ],
    series: [{ label: "commit" }, ...seriesList.map((s) => ({
      label: s.node, stroke: nodeColor(s.node), width: 2, spanGaps: true,
      points: { show: totalPoints <= 80, size: 8, stroke: nodeColor(s.node), fill: cssVar("--surface"), width: 2 },
    }))],
    hooks: {
      setCursor: [(u) => {
        const i = u.cursor.idx;
        if (i == null || !axis[i]) { tip.style.display = "none"; return; }
        const c = axis[i];
        const rows = seriesList.map((s, k) => {
          const v = ys[k][i];
          const prevIdx = ys[k].slice(0, i).map((val, j) => (val != null ? j : -1)).filter((j) => j >= 0).pop();
          const rel = v != null && prevIdx != null && ys[k][prevIdx] > 0 ? v / ys[k][prevIdx] - 1 : null;
          return h("div", { class: "row" }, h("span", { class: "key", style: `border-color:${nodeColor(s.node)}` }),
            h("span", { class: "v num" }, v == null ? "not measured" : `${fmtNum(v)} ${scale[0]}`),
            h("span", { class: "s num" }, rel == null ? "" : fmtPct(rel)), seriesList.length > 1 ? h("span", { class: "s" }, s.node) : null);
        });
        tip.replaceChildren(h("div", { class: "when" }, `${c.short} · ${fmtDate(c.date)}`), ...rows, h("div", { class: "subj" }, c.subject));
        tip.style.display = "block";
        const left = u.cursor.left, bbox = u.over.getBoundingClientRect(), wrapBox = wrap.getBoundingClientRect();
        const x = bbox.left - wrapBox.left + left + 14, maxX = wrap.clientWidth - tip.offsetWidth - 8;
        tip.style.left = `${Math.min(x, maxX)}px`;
        tip.style.top = `${bbox.top - wrapBox.top + 10}px`;
      }],
    },
  };
  state.chart = new uPlot(opts, [xs, ...ys], el);
  el.addEventListener("mouseleave", () => { tip.style.display = "none"; });
  el.addEventListener("click", () => { const i = state.chart.cursor.idx; if (i != null && axis[i]) location.hash = `#/c/${axis[i].sha}`; });
  const details = h("details", { class: "table-view" }, h("summary", {}, "Table view"),
    table(["commit", "date", ...seriesList.map((s) => `>${s.node} (${scale[0]})`)], axis.map((c, i) => [h("a", { href: `#/c/${c.sha}`, class: "mono" }, c.short), fmtDate(c.date), ...ys.map((col) => (col[i] == null ? "–" : fmtNum(col[i])))])));
  wrap.append(details);
  const onResize = () => { if (state.chart) state.chart.setSize({ width: Math.max(320, wrap.clientWidth - 16), height: 300 }); };
  window.addEventListener("resize", onResize, { once: false });
}

async function renderCommit(sha) {
  let c = state.commits.get(sha);
  if (!c) { c = await getJSON(`views/commits/${sha}.json`); state.commits.set(sha, c); }
  const filter = h("input", { type: "search", placeholder: "filter benchmarks", "aria-label": "filter benchmarks" });
  const minPct = h("select", {}, [0, 1, 2, 5, 10].map((p) => h("option", { value: p, selected: p === 0 }, p ? `≥ ${p}%` : "all changes")));
  const body = h("div", {});
  const draw = () => {
    const needle = filter.value.trim().toLowerCase(), min = Number(minPct.value) / 100;
    const rows = c.changes.filter((ch) => Math.abs(ch.rel_change) >= min && (!needle || `${ch.suite} ${ch.case} ${ch.params} ${ch.node}`.toLowerCase().includes(needle)));
    body.replaceChildren(table(["benchmark", "suite", "node", ">before", ">after", ">change", ">noise", ""], rows.map((ch) => [
      h("a", { href: benchHref({ ...ch }) }, caseLabel(ch)), ch.suite, ch.node, fmtValue(ch.prev_estimate, ch.unit), fmtValue(ch.estimate, ch.unit),
      delta(ch.rel_change, ch.direction), fmtAbsPct(ch.noise), gapTag(ch)]), { empty: "no changes match" }),
      h("p", { class: "note" }, `${rows.length} of ${c.changes.length} compared series`));
  };
  filter.addEventListener("input", draw); minPct.addEventListener("change", draw);
  const main = h("main", {},
    h("div", { class: "crumbs" }, h("a", { href: "#/" }, "home"), " / commit"),
    h("h2", {}, h("span", { class: "mono" }, c.short), " ", c.subject),
    h("p", { class: "lead" }, fmtDate(c.committer_date), " · ", h("a", { href: GITHUB + c.sha, target: "_blank", rel: "noopener" }, "on GitHub"),
      c.parent ? h("span", {}, " · parent ", h("a", { href: `#/c/${c.parent}`, class: "mono" }, c.parent.slice(0, 12))) : null),
    table(["node", "state", "reason", "started", "finished", ">suites ok", ">failed", ">results"], c.nodes.map((n) => [n.node, n.state, n.reason || "", fmtDate(n.started_at), fmtDate(n.finished_at), n.suites_ok, n.suites_failed || "0", n.results])),
    h("h3", {}, "Changes against the previous measured point"),
    h("div", { class: "filters" }, h("label", {}, "show", minPct), filter),
    body, footer());
  draw();
  return main;
}

function renderSuites() {
  const by = new Map();
  for (const s of state.series) {
    const e = by.get(s.suite) || { suite: s.suite, series: 0, cases: new Set(), nodes: new Set(), regressed: 0, improved: 0, latest: 0 };
    e.series++; e.cases.add(`${s.case}|${s.params}`); e.nodes.add(s.node);
    const v = verdict(s.vs_prev, s.direction, state.landing.notable);
    if (v === "regressed") e.regressed++; else if (v === "improved") e.improved++;
    e.latest = Math.max(e.latest, s.commit_order);
    by.set(s.suite, e);
  }
  const rows = [...by.values()].sort((a, b) => b.regressed - a.regressed || a.suite.localeCompare(b.suite));
  return h("main", {},
    h("div", { class: "crumbs" }, h("a", { href: "#/" }, "home"), " / suites"),
    h("h2", {}, "Suites"),
    h("p", { class: "lead" }, `${rows.length} benchmark binaries. Counts are series whose latest change exceeds ${state.landing.notable * 100}% against their previous point.`),
    table(["suite", ">benchmarks", ">series", "nodes", `>regressed`, `>improved`], rows.map((e) => [
      h("a", { href: `#/s/${encodeURIComponent(e.suite)}` }, e.suite), e.cases.size, e.series, [...e.nodes].sort().join(", "),
      e.regressed ? h("span", { class: "delta regressed" }, e.regressed) : "0", e.improved ? h("span", { class: "delta improved" }, e.improved) : "0"])),
    footer());
}

function footer() {
  const m = state.manifest;
  return h("footer", {}, `built ${m.built_at} · ${m.counts.points} results · tables: `, h("a", { href: `${DATA}points.parquet` }, "points.parquet"), ", ", h("a", { href: `${DATA}latest.parquet` }, "latest.parquet"), ", ", h("a", { href: `${DATA}changes.parquet` }, "changes.parquet"), ", ", h("a", { href: `${DATA}commits.parquet` }, "commits.parquet"), " · ", h("a", { href: "https://github.com/rok/benchx-arrow" }, "benchx-arrow"));
}

// ---- shell: header, search, theme, router -------------------------------------------

function header() {
  const input = h("input", { type: "search", placeholder: "search a benchmark…", "aria-label": "search a benchmark", autocomplete: "off" });
  const list = h("ul", { role: "listbox", hidden: true });
  let items = [], active = -1;
  const show = () => {
    const needle = input.value.trim().toLowerCase();
    list.replaceChildren(); active = -1;
    if (!needle) { list.hidden = true; return; }
    const seen = new Set();
    items = [];
    for (const s of state.series) {
      const key = `${s.suite}|${s.case}|${s.params}|${s.quantity}`;
      if (seen.has(key)) continue;
      if (`${s.case} ${s.params} ${s.suite}`.toLowerCase().includes(needle)) { seen.add(key); items.push(s); }
      if (items.length >= 14) break;
    }
    list.hidden = items.length === 0;
    items.forEach((s) => list.append(h("li", { role: "option", onclick: () => go(s) }, h("span", { class: "mono" }, caseLabel(s)), h("span", { class: "where" }, s.suite))));
  };
  const go = (s) => { input.value = ""; list.hidden = true; location.hash = benchHref(s); };
  input.addEventListener("input", show);
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") { active = Math.min(active + 1, items.length - 1); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); e.preventDefault(); }
    else if (e.key === "Enter" && active >= 0) { go(items[active]); return; }
    else if (e.key === "Escape") { list.hidden = true; return; }
    [...list.children].forEach((li, i) => li.setAttribute("aria-selected", i === active));
  });
  document.addEventListener("click", (e) => { if (!list.contains(e.target) && e.target !== input) list.hidden = true; });
  const theme = h("button", { class: "ghost", title: "theme: auto / dark / light", onclick: () => { const cur = document.documentElement.dataset.theme || "auto"; const next = cur === "auto" ? "dark" : cur === "dark" ? "light" : "auto"; applyTheme(next); route(); } }, "theme");
  return h("header", { class: "top" },
    h("h1", {}, h("a", { href: "#/" }, "Arrow benchmarks"), " ", h("span", { class: "sub" }, "benchx")),
    h("nav", {}, h("a", { href: "#/" }, "overview"), h("a", { href: "#/suites" }, "suites")),
    h("div", { class: "search" }, input, list), theme);
}

function applyTheme(mode) {
  if (mode === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = mode;
  try { if (mode === "auto") localStorage.removeItem("theme"); else localStorage.setItem("theme", mode); } catch (e) { /* storage unavailable */ }
}

async function route() {
  const root = document.getElementById("app");
  const hash = location.hash || "#/";
  const [path, qs] = hash.slice(1).split("?");
  const query = new URLSearchParams(qs || "");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  let view;
  try {
    if (parts[0] === "suites") view = renderSuites();
    else if (parts[0] === "s" && parts[1]) view = await renderSuite(parts[1]);
    else if (parts[0] === "b" && parts[2]) view = await renderBench(parts[1], parts.slice(2).join("/"), query);  // a case name may itself contain '/'
    else if (parts[0] === "c" && parts[1]) view = await renderCommit(parts[1]);
    else view = await renderLanding();
  } catch (e) {
    view = h("main", {}, h("h2", {}, "Could not load this view"), h("p", { class: "note" }, String(e.message || e)), h("p", {}, h("a", { href: "#/" }, "back to the overview")));
  }
  root.replaceChildren(view);
  window.scrollTo(0, 0);
}

async function init() {
  try { const t = localStorage.getItem("theme"); if (t) applyTheme(t); } catch (e) { /* ignore */ }
  const root = document.getElementById("app");
  try {
    state.manifest = await getJSON("manifest.json", { fresh: true });
    state.v = state.manifest.built_at;
    [state.landing, state.series] = await Promise.all([getJSON("views/landing.json"), getJSON("views/series.json")]);
  } catch (e) {
    root.replaceChildren(h("main", {}, h("h2", {}, "No data yet"), h("p", { class: "note" }, String(e.message || e))));
    return;
  }
  document.body.prepend(header());
  window.addEventListener("hashchange", route);
  await route();
}
init();
