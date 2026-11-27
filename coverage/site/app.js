"use strict";
/* YANG coverage dashboard — vanilla JS, lazy-rendered tree.
   Data: coverage.json produced by ../generate_coverage.py.
   Percentages are always covered/eligible descendant leaf counts
   from the JSON rollups — never averages of child percentages. */

const SPLIT_FILE = {
  train: "yang-learn/corpus/train.jsonl",
  dev: "yang-learn/corpus/dev.jsonl",
  test: "yang-learn/corpus/test.jsonl",
  benchmark: "yang-learn/corpus/benchmark.jsonl",
  held_out: "yang-learn/held_out.jsonl",
};
const REPO = "https://github.com/aag1091-alt/netconfig-slm/blob/main/";
const state = { overlay: "train", view: "all", priority: "",
                showExcluded: false, data: null, leafIndex: [] };
const $ = (id) => document.getElementById(id);

async function load() {
  for (const url of ["data/coverage.json", "../data/coverage.json"]) {
    try {
      const r = await fetch(url);
      if (r.ok) return await r.json();
    } catch (e) { /* try next */ }
  }
  throw new Error("coverage.json not found");
}

function overlayBit(overlay) {
  const splits = state.data.meta.splits;
  if (overlay === "corpus") return null; // bits 0..2 combined
  return splits.indexOf(overlay);
}
function leafCovered(leaf) {
  const mask = leaf[2];
  if (state.overlay === "corpus") return (mask & 0b00111) !== 0;
  return (mask & (1 << overlayBit(state.overlay))) !== 0;
}
function nodeCovered(node) { return (node.c && node.c[state.overlay]) || 0; }
function fmtPct(cov, total) {
  if (!total) return null;
  return (100 * cov / total).toFixed(2).replace(/\.?0+$/, "");
}

/* One pass over the parsed tree: per-node priority leaf totals
   (eligible leaves only), used by the priority filter. */
function annotate(node) {
  const prio = {};
  for (const leaf of node.lf || []) {
    if (leaf[4] && !(leaf[1] & 2)) prio[leaf[4]] = (prio[leaf[4]] || 0) + 1;
  }
  for (const ch of node.ch || []) {
    annotate(ch);
    for (const [k, v] of Object.entries(ch._prio)) prio[k] = (prio[k] || 0) + v;
  }
  node._prio = prio;
}

function nodeVisible(node) {
  const cov = nodeCovered(node);
  if (state.view === "covered" && cov === 0) return false;
  if (state.view === "uncovered" && node.t - cov === 0) return false;
  if (state.priority && !(node._prio[state.priority] > 0)) return false;
  return true;
}
function leafVisible(leaf) {
  const excluded = leaf[1] & 2;
  if (excluded && !state.showExcluded) return false;
  if (!excluded) {
    if (state.view === "covered" && !leafCovered(leaf)) return false;
    if (state.view === "uncovered" && leafCovered(leaf)) return false;
  }
  if (state.priority && leaf[4] !== state.priority) return false;
  return true;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function evidenceDetail(path, leaf) {
  const d = el("div", "detail");
  const meta = state.data.meta;
  const file = leaf[3] >= 0 ? meta.files[leaf[3]] : null;
  let line = `Path: ${path}`;
  if (file) line += ` · defined in ${file}`;
  if (leaf[1] & 1) line += " · leaf-list";
  if (leaf[1] & 4) line += " · deprecated (eligible)";
  d.append(el("div", null, line));
  const ev = leaf[7] || {};
  const splits = Object.keys(ev);
  if (splits.length) {
    const ul = el("ul");
    for (const s of splits) {
      const li = el("li");
      li.append(`${s}: ${ev[s].n} record(s) — `);
      ev[s].ids.forEach((id, i) => {
        const a = el("a", null, id);
        a.href = REPO + SPLIT_FILE[s];
        a.target = "_blank"; a.rel = "noopener";
        li.append(a);
        if (i < ev[s].ids.length - 1) li.append(", ");
      });
      if (ev[s].n > ev[s].ids.length) li.append(` (+${ev[s].n - ev[s].ids.length} more)`);
      ul.append(li);
    }
    d.append(ul);
  } else {
    d.append(el("div", null, "No corpus record exercises this leaf."));
  }
  if (leaf[5] > 0) {
    d.append(el("div", null,
      `Run-1 model on bench v1 items touching this leaf: ${leaf[6]}/${leaf[5]} passed (${fmtPct(leaf[6], leaf[5])}%).`));
  }
  return d;
}

function constructDetail(node) {
  const d = el("div", "detail");
  d.append(el("div", null,
    "Records that name this construct (container/list/choice/case) — they evidence the construct, not its leaves:"));
  const ul = el("ul");
  for (const [s, ids] of Object.entries(node.r)) {
    const li = el("li");
    li.append(`${s}: `);
    ids.slice(0, 6).forEach((id, i) => {
      const a = el("a", null, id);
      a.href = REPO + SPLIT_FILE[s];
      a.target = "_blank"; a.rel = "noopener";
      li.append(a);
      if (i < Math.min(ids.length, 6) - 1) li.append(", ");
    });
    if (ids.length > 6) li.append(` (+${ids.length - 6} more)`);
    ul.append(li);
  }
  d.append(ul);
  return d;
}

function renderLeaf(leaf, parentPath) {
  const li = el("li");
  li.setAttribute("role", "treeitem");
  const path = parentPath + "/" + leaf[0];
  const row = el("div", "row");
  const btn = el("button", "leafbtn", leaf[0]);
  btn.setAttribute("aria-expanded", "false");
  row.append(btn);
  const excluded = leaf[1] & 2;
  if (leaf[1] & 1) row.append(el("span", "chip", "leaf-list"));
  if (excluded) row.append(el("span", "chip excl", "excluded · obsolete"));
  else if (leafCovered(leaf)) row.append(el("span", "chip pass", "covered"));
  if (leaf[1] & 4) row.append(el("span", "chip", "deprecated"));
  if (leaf[4]) row.append(el("span", "chip prio", leaf[4]));
  if (leaf[5] > 0) row.append(el("span", "chip pass",
    `bench pass ${leaf[6]}/${leaf[5]}`));
  const ev = leaf[7] || {};
  const nEv = Object.values(ev).reduce((a, b) => a + b.n, 0);
  if (nEv) row.append(el("span", "counts", `${nEv} record(s)`));
  li.append(row);
  let detail = null;
  btn.addEventListener("click", () => {
    if (detail) { detail.remove(); detail = null; btn.setAttribute("aria-expanded", "false"); }
    else {
      detail = evidenceDetail(path, leaf);
      li.append(detail);
      btn.setAttribute("aria-expanded", "true");
    }
  });
  return li;
}

function renderNode(node, path) {
  const li = el("li");
  li.setAttribute("role", "treeitem");
  li.dataset.path = path;
  const row = el("div", "row");
  const hasKids = (node.ch && node.ch.length) || (node.lf && node.lf.length);
  const toggle = el("button", "toggle", hasKids ? "▸" : "·");
  toggle.setAttribute("aria-label", `expand ${node.n || "root"}`);
  row.append(toggle);
  row.append(el("span", "name", node.n || "(root)"));
  const cov = nodeCovered(node);
  const counts = el("span", "counts");
  if (node.t === 0) {
    counts.innerHTML = "";
    counts.append(el("span", "na",
      `n/a — no eligible leaves${node.x ? ` (${node.x} excluded)` : ""}`));
  } else {
    const b = el("b", null, `${cov}/${node.t}`);
    counts.append(b, document.createTextNode(` · ${fmtPct(cov, node.t)}%`));
    const bar = el("span", "bar");
    const fill = el("i");
    fill.style.width = `${100 * cov / node.t}%`;
    bar.append(fill);
    row.append(bar);
  }
  row.append(counts);
  if (node.x) row.append(el("span", "chip excl", `${node.x} excluded`));
  if (node.r) {
    const n = Object.values(node.r).reduce((a, b) => a + b.length, 0);
    row.append(el("span", "chip", `${n} construct ref(s)`));
  }
  li.append(row);
  let kids = null, detail = null;
  const expand = () => {
    if (kids) return;
    kids = el("ul");
    kids.setAttribute("role", "group");
    for (const ch of node.ch || []) {
      if (nodeVisible(ch)) kids.append(renderNode(ch, path + "/" + ch.n));
    }
    for (const leaf of node.lf || []) {
      if (leafVisible(leaf)) kids.append(renderLeaf(leaf, path));
    }
    if (node.r) {
      const wrap = el("li");
      const rb = el("button", "leafbtn", "construct references…");
      rb.addEventListener("click", () => {
        if (detail) { detail.remove(); detail = null; }
        else { detail = constructDetail(node); wrap.append(detail); }
      });
      wrap.append(rb);
      kids.append(wrap);
    }
    li.append(kids);
    toggle.textContent = "▾";
    toggle.setAttribute("aria-expanded", "true");
  };
  const collapse = () => {
    if (kids) { kids.remove(); kids = null; }
    toggle.textContent = "▸";
    toggle.setAttribute("aria-expanded", "false");
  };
  toggle.addEventListener("click", () => (kids ? collapse() : expand()));
  li._expand = expand;
  return li;
}

function renderTree() {
  const tree = $("tree");
  tree.replaceChildren();
  const root = state.data.tree;
  for (const ch of root.ch || []) {
    if (nodeVisible(ch)) tree.append(renderNode(ch, "/" + ch.n));
  }
  $("tree-status").textContent =
    `Denominator: ${state.data.meta.denominator.eligible_leaves.toLocaleString()} eligible leaves under /native. ` +
    `Percentages are descendant leaf counts for the “${state.overlay}” overlay.`;
}

function buildLeafIndex(node, path) {
  for (const leaf of node.lf || []) {
    state.leafIndex.push({ path: path + "/" + leaf[0], leaf });
  }
  for (const ch of node.ch || []) buildLeafIndex(ch, path + "/" + ch.n);
}

function doSearch(q) {
  const box = $("search-results");
  q = q.trim().toLowerCase();
  if (q.length < 3) { box.hidden = true; box.replaceChildren(); return; }
  const hits = state.leafIndex.filter(h => h.path.toLowerCase().includes(q));
  box.replaceChildren();
  box.append(el("strong", null,
    `${hits.length.toLocaleString()} matching leaves (showing first 200)`));
  const ul = el("ul");
  for (const h of hits.slice(0, 200)) {
    const li = el("li");
    const excluded = h.leaf[1] & 2;
    li.append(el("code", null, h.path));
    li.append(document.createTextNode(
      excluded ? " — excluded (obsolete)" :
      leafCovered(h.leaf) ? " — covered" : " — not covered"));
    if (h.leaf[5] > 0) li.append(document.createTextNode(
      ` · bench pass ${h.leaf[6]}/${h.leaf[5]}`));
    ul.append(li);
  }
  box.append(ul);
  box.hidden = false;
}

function statCard(value, label, unknown) {
  const d = el("div", "stat" + (unknown ? " unknown" : ""));
  d.append(el("div", "v", value), el("div", "k", label));
  return d;
}

function renderPanels() {
  const m = state.data.meta, d = m.denominator;
  const h = $("headline");
  h.replaceChildren();
  h.append(
    statCard(d.eligible_leaves.toLocaleString(),
      "eligible leaves in the denominator (config-true, not obsolete)"),
    statCard(`${d.covered_train.toLocaleString()} · ${d.pct_train}%`,
      "covered by the training split"),
    statCard(`${d.covered_corpus_train_dev_test.toLocaleString()} · ${d.pct_corpus}%`,
      "covered by corpus train+dev+test"),
    statCard(d.interface_type_normalized_denominator.toLocaleString(),
      "denominator if interface-type replicas are counted once (labelled view, not the headline)"),
    statCard(`${m.overall_pass_bench_v1.pass}/${m.overall_pass_bench_v1.n} · ${m.overall_pass_bench_v1.pct}%`,
      "run-1 model pass on bench v1 (all items)"),
    statCard("not 0%", "OpenConfig / IETF / MIB / wireless surfaces are not inventoried — see “What is not counted”", true),
  );

  const ps = $("priorities");
  ps.replaceChildren(el("h2", null, "Priority groups (heuristic labels)"));
  ps.append(el("p", "subtitle",
    "Maintainer judgement about what matters most for a switch-configuration model — editable in coverage/priorities.yaml. Not telemetry; no usage data exists or is claimed. Counts use the training overlay."));
  const t = el("table");
  t.innerHTML = "<tr><th>Group</th><th class='num'>Eligible leaves</th>" +
    "<th class='num'>Covered (train)</th><th class='num'>%</th></tr>";
  for (const p of m.priorities) {
    const tr = el("tr");
    const td = el("td", null, `${p.label} `);
    const btn = el("button", "leafbtn", "filter");
    btn.addEventListener("click", () => {
      state.priority = p.id; $("priority").value = p.id; renderTree();
    });
    td.append(btn);
    tr.append(td, el("td", "num", p.total.toLocaleString()),
      el("td", "num", p.covered_train.toLocaleString()),
      el("td", "num", p.pct_train === null ? "n/a" : p.pct_train + "%"));
    t.append(tr);
  }
  ps.append(t);

  const fs = $("families");
  fs.replaceChildren(el("h2", null, "Run-1 model pass rates — bench v1, by family"));
  fs.append(el("p", "subtitle",
    `Mechanical graders (score_bench.py). Per-leaf pass is shown in the tree where an item's paths join (${m.evidence_resolution.pass_items_joined_to_leaves} of ${m.evidence_resolution.pass_items_total} items); the rest are reported here at family level only.`));
  const ft = el("table");
  ft.innerHTML = "<tr><th>Family</th><th class='num'>Items</th><th class='num'>Pass</th><th class='num'>%</th></tr>";
  for (const [fam, v] of Object.entries(m.family_pass_bench_v1)) {
    const tr = el("tr");
    tr.append(el("td", null, fam), el("td", "num", String(v.n)),
      el("td", "num", String(v.pass)), el("td", "num", v.pct + "%"));
    ft.append(tr);
  }
  fs.append(ft);

  const cs = $("categories");
  cs.replaceChildren(el("h2", null, "What is not counted"));
  cs.append(el("p", "subtitle",
    "Every exclusion is deliberate and counted. “Not inventoried” means unmeasured — it is not 0% coverage."));
  const ct = el("table");
  ct.innerHTML = "<tr><th>Category</th><th class='num'>Count</th><th>Treatment</th></tr>";
  const rows = [
    ["Obsolete leaves in the /native tree", d.excluded.obsolete_leaves, "Excluded from denominator (device still serves many; tracked separately)"],
    ["Config-false leaves in the /native tree", d.excluded.config_false_leaves_in_native_tree, "Excluded from denominator (none exist in this tree; state lives in *-oper modules)"],
    ["Deprecated leaves", d.excluded.deprecated_leaves_included_in_denominator, "Included in denominator (still supported)"],
    ["Duplicate canonical paths collapsed (choice-case collisions)", d.excluded.duplicate_transparent_paths_collapsed, "Counted once"],
    ["deviate not-supported statements (advertised deviations)", d.excluded.deviate_not_supported_statements, "Nodes removed by the device profile; absent from the tree"],
  ];
  const census = d.module_census.implement_by_class;
  const classRows = [
    ["oper (operational state)", "Excluded by rule — operational state, not configuration"],
    ["rpc", "Excluded by rule — RPCs have no config leaves"],
    ["events (notifications)", "Excluded by rule — notifications"],
    ["types only (no data nodes)", "No data nodes to count"],
    ["SNMP MIB module", "Not inventoried — legacy MIB models"],
    ["OpenConfig (separate model)", "Not inventoried — separate model surface"],
    ["IETF (separate/infrastructure model)", "Not inventoried — separate/infrastructure models"],
    ["tailf (server infrastructure)", "Not inventoried — server infrastructure"],
    ["other vendor/helper", "Not inventoried"],
  ];
  for (const [label, n, how] of rows) {
    const tr = el("tr");
    tr.append(el("td", null, label), el("td", "num", Number(n).toLocaleString()),
      el("td", null, how));
    ct.append(tr);
  }
  for (const [label, how] of classRows) {
    if (!(label in census)) continue;
    const tr = el("tr", "unknown");
    tr.append(el("td", null, `${label} modules`),
      el("td", "num", census[label].toLocaleString()),
      el("td", null, how));
    ct.append(tr);
  }
  const roots = d.non_native_roots_counted_not_inventoried;
  const tr = el("tr", "unknown");
  tr.append(el("td", null, "Standalone config roots outside /native (e.g. wireless *-cfg)"),
    el("td", "num", `${roots.length} roots · ${roots.reduce((a, r) => a + r.eligible_leaves, 0).toLocaleString()} eligible leaves`),
    el("td", null, "Counted, not inventoried — outside the native-model scope"));
  ct.append(tr);
  cs.append(ct);
  const det = el("details");
  det.append(el("summary", null, "Non-/native roots, by module"));
  const ul = el("ul");
  for (const r of roots) {
    ul.append(el("li", null, `${r.module} ${r.root} — ${r.eligible_leaves} eligible leaves`));
  }
  det.append(ul);
  cs.append(det);

  const p = m.provenance;
  $("provenance").innerHTML = "";
  $("provenance").append(
    el("p", null, `Provenance: ${p.bundle}. Inventory tool: ${p.tool}. Load: ${p.inventory_load} (${p.loaded_module_files} module files). Corpus: ${p.corpus}. Scores: ${p.model_scores}.`),
    el("p", null, "Refresh: re-run coverage/generate_coverage.py, commit the regenerated coverage/data/coverage.json, and GitHub Pages republishes. See coverage/README.md."),
  );
  if (m.generated_at) $("provenance").append(el("p", null, `Generated ${m.generated_at}.`));
}

function wire() {
  $("overlay").addEventListener("change", (e) => { state.overlay = e.target.value; renderTree(); });
  $("view").addEventListener("change", (e) => { state.view = e.target.value; renderTree(); });
  $("priority").addEventListener("change", (e) => { state.priority = e.target.value; renderTree(); });
  $("show-excluded").addEventListener("change", (e) => { state.showExcluded = e.target.checked; renderTree(); });
  let timer = null;
  $("search").addEventListener("input", (e) => {
    clearTimeout(timer);
    timer = setTimeout(() => doSearch(e.target.value), 180);
  });
  $("tree").addEventListener("keydown", (e) => {
    const rows = [...$("tree").querySelectorAll(".toggle, .leafbtn")];
    const i = rows.indexOf(document.activeElement);
    if (i < 0) return;
    if (e.key === "ArrowDown" && rows[i + 1]) { rows[i + 1].focus(); e.preventDefault(); }
    if (e.key === "ArrowUp" && rows[i - 1]) { rows[i - 1].focus(); e.preventDefault(); }
    if (e.key === "ArrowRight" && document.activeElement.classList.contains("toggle")) {
      document.activeElement.click(); e.preventDefault();
    }
  });
}

load().then((data) => {
  state.data = data;
  annotate(data.tree);
  buildLeafIndex(data.tree, "");
  const sel = $("priority");
  for (const p of data.meta.priorities) {
    const o = el("option", null, p.label);
    o.value = p.id;
    sel.append(o);
  }
  renderPanels();
  renderTree();
  wire();
}).catch((err) => {
  $("headline").innerHTML = "";
  $("headline").append(el("p", "loading",
    `Could not load coverage data (${err.message}). If you opened this file directly, serve the coverage/ directory over HTTP instead.`));
});
