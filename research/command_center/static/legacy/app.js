"use strict";
/* Research Command Centre front end. No build step, no external resources. Research/simulation only. */
const ROLE_IDS = ["lead", "data_ibkr", "strategy_researcher", "backtester", "validator", "risk_execution", "monitor"];
const ROLE_NAMES = {lead: "Lead", data_ibkr: "Data (IBKR)", strategy_researcher: "Strategy Researcher", backtester: "Backtester",
  validator: "Validator", risk_execution: "Risk & Execution", monitor: "Monitor"};
const ST_ICON = {working: "▶", queued: "…", awaiting_dependency: "⧗", awaiting_approval: "?", idle: "–", failed: "✕", complete: "✓", none: "∅"};
const ST_LABEL = s => (s || "no events yet").replace(/_/g, " ").toUpperCase();
const SVGNS = "http://www.w3.org/2000/svg";
const PULSE_MS = 3200;
const NAV = [
  ["overview", "Overview", "agents + graph"], ["data", "Data centre", "not implemented"], ["library", "Research library", "docs index"],
  ["strategy", "Strategy registry", "not implemented"], ["backtest", "Backtest lab", "results index"], ["montecarlo", "Monte Carlo lab", "not implemented"],
  ["execution", "Execution console", "blocked"], ["risk", "Risk centre", "not implemented"], ["audit", "Audit / settings", "partial"]];
const PREREQ = {
  data: "a read-only market-data ingestion pipeline that emits data/quality events (the IBKR connector is authorised for market data only) and a data-quality results store. See docs/DATA_QUALITY.md and docs/DATA_REQUEST.md.",
  strategy: "a versioned, hashed registry of frozen strategy specs and their parameter-search budget accounting. Nothing is registered in this slice; see docs/ for the written spec.",
  montecarlo: "Monte Carlo result artifacts from src/tradelab/validation plus a job runner. Note: Monte Carlo resamples the same evidence and adds no independent market evidence.",
  execution: "an approved broker adapter, in-code risk limits (max loss/day, max position, kill switch) with tests, and explicit human approval. This app has no broker/trading capability and imports none.",
  risk: "risk-limit configuration, a kill-switch implementation enforced in code and tests, and events reporting risk state. None exist in this slice."};

const S = {
  runs: [], run: null, snap: null, events: [], seqs: new Set(), view: "overview", tab: "feed", selected: null,
  conn: "connecting", lastSeen: null, staleMs: 15000, es: null, esRun: null, pulses: new Map(), loading: true, error: null,
  rp: {active: false, playing: false, speed: 1, idx: 0, sched: [], virt: 0, timer: null}, snapTok: 0, snapTimer: null,
  view0: {x: 0, y: 0, k: 1}, searchRes: null, cmdResult: null, newSeqs: new Set(),
};
const $ = s => document.querySelector(s);
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v; else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) el.append(c.nodeType ? c : document.createTextNode(String(c)));
  return el;
}
function sv(tag, attrs, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null) el.setAttribute(k, v);
  for (const c of kids.flat()) if (c != null) el.append(c.nodeType ? c : document.createTextNode(String(c)));
  return el;
}
const ls = {get: k => { try { return localStorage.getItem(k); } catch (e) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} }};
const ss = {get: k => { try { return sessionStorage.getItem(k); } catch (e) { return null; } }, set: (k, v) => { try { sessionStorage.setItem(k, v); } catch (e) {} }};
const hms = ms => new Date(ms).toTimeString().slice(0, 8);
const tsHms = iso => iso ? new Date(iso).toTimeString().slice(0, 8) : "–";
function dur(sec) { if (sec == null || sec < 0) return "–"; sec = Math.floor(sec); const m = Math.floor(sec / 60), s = sec % 60; return m >= 60 ? `${Math.floor(m / 60)}h${m % 60}m` : m ? `${m}m${s}s` : `${s}s`; }
async function api(path, opts) {
  const r = await fetch(path, opts);
  let body = null; try { body = await r.json(); } catch (e) {}
  if (!r.ok && !(body && body.recorded)) { const e = new Error((body && body.error) || `HTTP ${r.status}`); e.status = r.status; throw e; }
  return body;
}
function announce(t) { $("#live").textContent = t; }

/* ---------------- connection / stale ---------------- */
function setConn(c) {
  if (S.conn === c) return;
  S.conn = c; renderConn();
}
function renderConn() {
  const b = $("#conn"), c = S.conn;
  const t = {connecting: "○ CONNECTING", live: "● LIVE", reconnecting: "↻ RECONNECTING", stale: "⚠ STALE"}[c];
  b.className = "badge conn " + c; b.textContent = t;
  const sb = $("#banner-stale");
  document.body.classList.toggle("stale", c === "stale");
  sb.hidden = c !== "stale";
  if (c === "stale") sb.textContent = `STALE: showing last known state as of ${S.lastSeen ? hms(S.lastSeen) : "never"}`;
  announce(c === "stale" ? sb.textContent : "Connection " + c);
}
function seen() { S.lastSeen = Date.now(); if (S.conn !== "live") setConn("live"); }
setInterval(() => {
  if (S.rp.active) return;
  if (S.conn !== "connecting" && S.lastSeen && Date.now() - S.lastSeen > S.staleMs) { setConn("stale"); renderConn(); }
  else if (S.conn === "stale") renderConn();
  if (S.snap && S.view === "overview") updateGraph();
}, 1000);

function closeStream() { if (S.es) { S.es.close(); S.es = null; } }
function openStream() {
  closeStream();
  if (!S.run) return;
  const maxSeq = S.events.length ? S.events[S.events.length - 1].seq : 0;
  const es = new EventSource(`/api/stream?run_id=${encodeURIComponent(S.run)}&last_event_id=${maxSeq}`);
  S.es = es;
  es.onopen = () => { seen(); scheduleSnapshot(); };
  es.addEventListener("cc", ev => { seen(); onEvent(JSON.parse(ev.data), true); });
  es.addEventListener("ping", ev => { seen(); try { S.lastServerTime = JSON.parse(ev.data).server_time; } catch (e) {} });
  es.onerror = () => {
    if (S.conn !== "stale") setConn("reconnecting");
    if (es.readyState === 2) setTimeout(() => { if (S.es === es) openStream(); }, 2000);
  };
}

/* ---------------- data ---------------- */
function onEvent(e, live) {
  if (S.seqs.has(e.seq)) return;                       // dedupe by seq
  S.seqs.add(e.seq);
  const i = S.events.length;
  if (!i || S.events[i - 1].seq < e.seq) S.events.push(e); else { S.events.push(e); S.events.sort((a, b) => a.seq - b.seq); }
  if (live) {
    S.newSeqs.add(e.seq); setTimeout(() => S.newSeqs.delete(e.seq), 4000);
    if (e.event_type === "message.sent" && !S.rp.active) pulse(e.payload.sender, e.payload.recipient, e.payload.kind);
    if (!S.rp.active) { scheduleSnapshot(); if (S.view === "overview") renderTabs(true); }
  }
}
function visibleEvents() { return S.rp.active ? S.events.filter(e => e.seq <= S.rp.cursorSeq) : S.events; }
function scheduleSnapshot() { clearTimeout(S.snapTimer); S.snapTimer = setTimeout(loadSnapshot, 120); }
async function loadSnapshot() {
  const tok = ++S.snapTok;
  try {
    let url = `/api/snapshot?run_id=${encodeURIComponent(S.run || "")}`;
    if (S.rp.active) url += `&upto_seq=${S.rp.cursorSeq || 0}`;
    const snap = await api(url);
    if (tok !== S.snapTok) return;
    S.snap = snap; S.error = null; S.loading = false;
  } catch (e) { S.error = "Could not load snapshot: " + e.message; S.loading = false; }
  renderAll();
}
async function loadRuns() {
  const r = await api("/api/runs");
  S.runs = r.runs;
  if (!S.run || !r.runs.some(x => x.run_id === S.run)) S.run = ls.get("cc.run") && r.runs.some(x => x.run_id === ls.get("cc.run")) ? ls.get("cc.run") : r.latest;
  const sel = $("#run-select"); sel.textContent = "";
  if (!r.runs.length) sel.append(h("option", {value: ""}, "(no runs yet)"));
  for (const x of r.runs) sel.append(h("option", {value: x.run_id}, `${x.run_id} (${x.n})${x.has_demo ? " DEMO" : ""}`));
  sel.value = S.run || "";
}
async function loadRun(run) {
  stopReplay(true);
  S.run = run; if (run) ls.set("cc.run", run);
  S.events = []; S.seqs = new Set(); S.snap = null; S.loading = true; S.selected = null; renderAll();
  try {
    if (run) {
      let after = 0;
      for (;;) {
        const r = await api(`/api/events?run_id=${encodeURIComponent(run)}&after_seq=${after}&limit=2000`);
        r.events.forEach(e => onEvent(e, false));
        if (r.events.length < 2000) break; after = r.events[r.events.length - 1].seq;
      }
    }
    await loadSnapshot();
    openStream();
  } catch (e) { S.error = "Could not load events: " + e.message; S.loading = false; setConn("reconnecting"); renderAll(); setTimeout(() => loadRun(S.run), 3000); }
}
async function init() {
  S.staleMs = 15000;
  try { const hl = await api("/api/health"); S.staleMs = (hl.stale_after_s || 15) * 1000; } catch (e) {}
  try { await loadRuns(); } catch (e) { S.error = "Server unreachable: " + e.message; S.loading = false; renderAll(); setConn("reconnecting"); setTimeout(init, 2000); return; }
  await loadRun(S.run);
}

/* ---------------- header / nav ---------------- */
function renderHeader() {
  const s = S.snap;
  const mode = S.rp.active ? "REPLAY" : (s ? s.mode : "…");
  const mb = $("#mode-badge"); mb.textContent = "MODE: " + mode + (s && s.mode_is_default && !S.rp.active ? " (default label)" : "");
  $("#banner-demo").hidden = !(s && s.has_demo);
  const bar = $("#modebar"); bar.textContent = "";
  bar.append(h("span", {class: "note"}, "Mode:"));
  for (const m of ["DEMO", "BACKTEST", "REPLAY"]) bar.append(h("button", {class: m === mode ? "sel" : "", "aria-pressed": String(m === mode), onclick: () => setMode(m)}, m));
  bar.append(h("button", {"aria-disabled": "true", title: "not implemented in this slice: requires a market-data feed and a shadow-order recorder", onclick: () => cmdNote("SHADOW: not implemented in this slice")}, "SHADOW"));
  for (const m of ["PAPER", "LIVE"]) bar.append(h("button", {"aria-disabled": "true", title: "blocked: requires approval & broker adapter", onclick: () => cmdNote(`${m}: blocked: requires approval & broker adapter`)}, `${m} (blocked)`));
  bar.append(h("span", {class: "note", id: "mode-note"}, "PAPER/LIVE: blocked: requires approval & broker adapter. Mode is a display label; no trading capability exists."));
}
function cmdNote(t) { const n = $("#mode-note"); if (n) { n.textContent = t; n.className = "note err"; } announce(t); }
function renderNav() {
  const nav = $("#nav"); nav.textContent = "";
  for (const [id, name, tag] of NAV) nav.append(h("a", {href: "#" + id, "aria-current": S.view === id ? "page" : null}, name, h("span", {class: "tag"}, tag)));
}
function route() { const v = location.hash.slice(1) || "overview"; S.view = NAV.some(n => n[0] === v) ? v : "overview"; renderNav(); renderView(); }
window.addEventListener("hashchange", route);

/* ---------------- graph ---------------- */
const POS = {lead: [0, 0]};
ROLE_IDS.slice(1).forEach((id, i) => POS[id] = [((i % 3) - 1) * 200, i < 3 ? 230 : 410]);
const TW = 160, TH = 110;
let G = null;
function initGraph() {
  const wrap = h("div", {id: "graph-wrap"});
  const svg = sv("svg", {id: "graph", role: "application", "aria-label": "Agent graph. Tab to move between towers, arrow keys to navigate, Enter for details, plus and minus to zoom, 0 to fit.", tabindex: "0"});
  const defs = sv("defs", {}, sv("marker", {id: "arr", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse"}, sv("path", {d: "M0,0 L10,5 L0,10 z", fill: "var(--accent)"})),
    sv("marker", {id: "arrd", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse"}, sv("path", {d: "M0,0 L10,5 L0,10 z", fill: "var(--wait)"})));
  const root = sv("g", {id: "g-root"}), gConn = sv("g"), gDep = sv("g"), gMsg = sv("g"), gTow = sv("g");
  root.append(gConn, gDep, gMsg, gTow); svg.append(defs, root); wrap.append(svg);
  const bus = 115;
  const bus2 = 325, trunk = -300;
  ROLE_IDS.slice(1).forEach((id, i) => gConn.append(sv("path", {class: "conn-line", d: i < 3 ? `M0,${TH / 2} V${bus} H${POS[id][0]} V${POS[id][1] - TH / 2 - 8}` : `M0,${bus} H${trunk} V${bus2} H${POS[id][0]} V${POS[id][1] - TH / 2 - 8}`})));
  const towers = {};
  for (const id of ROLE_IDS) {
    const [x, y] = POS[id];
    const g = sv("g", {class: "tower", tabindex: "0", role: "button", "data-agent": id, transform: `translate(${x},${y})`});
    const crenel = [-TW / 2, -TW / 2 + 40, -TW / 2 + 80, -TW / 2 + 120].map(cx => sv("rect", {class: "top", x: cx, y: -TH / 2 - 10, width: 40, height: 10}));
    const els = {
      body: sv("rect", {class: "body", x: -TW / 2, y: -TH / 2, width: TW, height: TH, rx: 4}),
      icon: sv("text", {class: "st", x: -TW / 2 + 8, y: -TH / 2 + 18}), role: sv("text", {class: "role", x: -TW / 2 + 8, y: -TH / 2 + 38}),
      task: sv("text", {class: "sm", x: -TW / 2 + 8, y: -TH / 2 + 56}), l1: sv("text", {class: "sm", x: -TW / 2 + 8, y: -TH / 2 + 72}),
      l2: sv("text", {class: "sm", x: -TW / 2 + 8, y: -TH / 2 + 87}), demo: sv("text", {class: "demo-tag", x: TW / 2, y: -TH / 2 - 14, "text-anchor": "end"}),
      title: sv("title"),
    };
    g.append(...crenel, els.body, els.icon, els.role, els.task, els.l1, els.l2, els.demo, els.title);
    g.addEventListener("click", () => selectAgent(id));
    g.addEventListener("keydown", ev => towerKey(ev, id));
    gTow.append(g); towers[id] = {g, els};
  }
  G = {wrap, svg, root, gDep, gMsg, towers, edges: new Map(), deps: new Map()};
  svg.addEventListener("wheel", ev => { ev.preventDefault(); zoomAt(ev.deltaY < 0 ? 1.12 : 1 / 1.12, ev.clientX, ev.clientY); }, {passive: false});
  let drag = null;
  svg.addEventListener("pointerdown", ev => { if (ev.target.closest(".tower")) return; drag = {x: ev.clientX, y: ev.clientY, vx: S.view0.x, vy: S.view0.y}; svg.setPointerCapture(ev.pointerId); });
  svg.addEventListener("pointermove", ev => { if (!drag) return; S.view0.x = drag.vx + ev.clientX - drag.x; S.view0.y = drag.vy + ev.clientY - drag.y; applyView(); });
  svg.addEventListener("pointerup", () => drag = null);
  svg.addEventListener("keydown", ev => {
    if (ev.target !== svg) return;
    if (ev.key === "+" || ev.key === "=") { zoomAt(1.2); ev.preventDefault(); } else if (ev.key === "-") { zoomAt(1 / 1.2); ev.preventDefault(); }
    else if (ev.key === "0") { fitGraph(); ev.preventDefault(); } else if (ev.key.startsWith("Arrow") && !ev.target.closest(".tower")) { panKey(ev); }
  });
  const tools = h("div", {class: "gtools"}, h("button", {onclick: () => zoomAt(1.2), "aria-label": "Zoom in"}, "+"), h("button", {onclick: () => zoomAt(1 / 1.2), "aria-label": "Zoom out"}, "−"),
    h("button", {onclick: fitGraph, "aria-label": "Auto layout / fit to view"}, "Auto-layout"));
  wrap.append(tools, h("div", {class: "glegend"}, "solid → = message / delegation (pulses when one arrives) · dashed = task dependency · grey elbows = org structure"));
  new ResizeObserver(() => { if (G && (!G.fitted || S.view0.auto)) fitGraph(); }).observe(wrap);
}
function panKey(ev) { const d = 40; if (ev.key === "ArrowLeft") S.view0.x += d; if (ev.key === "ArrowRight") S.view0.x -= d; if (ev.key === "ArrowUp") S.view0.y += d; if (ev.key === "ArrowDown") S.view0.y -= d; applyView(); ev.preventDefault(); }
function applyView() { const v = S.view0; G.root.setAttribute("transform", `translate(${v.x},${v.y}) scale(${v.k})`); }
function fitGraph() {
  const r = G.svg.getBoundingClientRect(); if (!r.width) return;
  const w = 640, hgt = 410 + TH + 60; const k = Math.min(r.width / w, r.height / hgt, 1.4);
  S.view0 = {k, x: r.width / 2 + 25 * k, y: (r.height - hgt * k) / 2 + (TH / 2 + 28) * k, auto: true}; G.fitted = true; applyView();
}
function zoomAt(f, cx, cy) {
  const r = G.svg.getBoundingClientRect(); cx = (cx == null ? r.left + r.width / 2 : cx) - r.left; cy = (cy == null ? r.top + r.height / 2 : cy) - r.top;
  const v = S.view0, nk = Math.max(0.3, Math.min(3, v.k * f)), real = nk / v.k;
  v.x = cx - (cx - v.x) * real; v.y = cy - (cy - v.y) * real; v.k = nk; v.auto = false; applyView();
}
function towerKey(ev, id) {
  const idx = ROLE_IDS.indexOf(id); let to = null;
  if (ev.key === "Enter" || ev.key === " ") { selectAgent(id); ev.preventDefault(); return; }
  if (ev.key === "Escape") { S.selected = null; renderPanel(); return; }
  if (ev.key === "ArrowRight") to = id === "lead" ? ROLE_IDS[1] : ROLE_IDS[Math.min(ROLE_IDS.length - 1, idx + 1)];
  else if (ev.key === "ArrowLeft") to = id === "lead" ? null : ROLE_IDS[Math.max(1, idx - 1)];
  else if (ev.key === "ArrowDown") to = id === "lead" ? ROLE_IDS[3] : null;
  else if (ev.key === "ArrowUp") to = id === "lead" ? null : "lead";
  if (to) { G.towers[to].g.focus(); ev.preventDefault(); }
}
function edgePath(a, b, off) {
  const [ax, ay] = POS[a], [bx, by] = POS[b]; let dx = bx - ax, dy = by - ay; const len = Math.hypot(dx, dy) || 1; dx /= len; dy /= len;
  const trim = (ux, uy) => { const t = Math.min(ux ? (TW / 2 + 6) / Math.abs(ux) : 1e9, uy ? (TH / 2 + 6) / Math.abs(uy) : 1e9); return t; };
  const t = trim(dx, dy);
  const sx = ax + dx * t, sy = ay + dy * t, ex = bx - dx * t, ey = by - dy * t;
  const mx = (sx + ex) / 2 - dy * off, my = (sy + ey) / 2 + dx * off;
  return `M${sx.toFixed(1)},${sy.toFixed(1)} Q${mx.toFixed(1)},${my.toFixed(1)} ${ex.toFixed(1)},${ey.toFixed(1)}`;
}
function pulse(from, to, kind) {
  if (!G || !POS[from] || !POS[to]) return;
  const key = from + ">" + to; ensureMsgEdge(key, from, to);
  S.pulses.set(key, Date.now() + PULSE_MS);
  const ed = G.edges.get(key); ed.path.classList.add("pulse");
  if (ed.dot) ed.dot.remove();
  ed.dot = sv("circle", {class: "dot", r: 5, style: `offset-path:path('${ed.d}')`}); ed.g.append(ed.dot);
  announce(`${kind === "delegation" ? "Delegation" : "Message"} from ${ROLE_NAMES[from]} to ${ROLE_NAMES[to]}`);
  setTimeout(() => { if ((S.pulses.get(key) || 0) <= Date.now()) { ed.path.classList.remove("pulse"); if (ed.dot) { ed.dot.remove(); ed.dot = null; } } }, PULSE_MS + 50);
}
function ensureMsgEdge(key, from, to) {
  if (G.edges.has(key)) return G.edges.get(key);
  const d = edgePath(from, to, 34), g = sv("g", {"data-edge": key});
  const path = sv("path", {class: "edge-msg", d, "marker-end": "url(#arr)", "data-edge": key}); const label = sv("text", {class: "edge-label"});
  g.append(path, label); G.gMsg.append(g);
  const e = {g, path, label, d, dot: null}; G.edges.set(key, e); return e;
}
function updateGraph() {
  if (!G || !S.snap) return;
  const s = S.snap, ref = S.rp.active ? Date.parse(s.reference_time) : Date.now();
  for (const a of s.agents) {
    const t = G.towers[a.agent_id], st = a.status || "none";
    t.g.setAttribute("class", `tower s-${st}${S.selected === a.agent_id ? " sel" : ""}`);
    t.els.icon.textContent = `${ST_ICON[st]} ${ST_LABEL(a.status)}`;
    t.els.role.textContent = ROLE_NAMES[a.agent_id];
    t.els.task.textContent = a.task ? a.task.slice(0, 28) : (a.has_events ? "(no task reported)" : "no events from this agent");
    const since = a.status_since ? (ref - Date.parse(a.status_since)) / 1000 : null;
    t.els.l1.textContent = a.has_events ? `in status ${dur(since)} · done ${a.completed_count}` : "";
    t.els.l2.textContent = a.last_activity ? `last ${tsHms(a.last_activity)}${a.blockers.length ? " · blocked " + a.blockers.length : ""}` : "";
    t.els.demo.textContent = a.demo ? "DEMO" : "";
    t.els.title.textContent = `${ROLE_NAMES[a.agent_id]}: ${ST_LABEL(a.status)}${a.task ? " – " + a.task : ""}${a.demo ? " (DEMO data)" : ""}`;
    t.g.setAttribute("aria-label", t.els.title.textContent + (a.blockers.length ? `; blockers: ${a.blockers.join("; ")}` : ""));
    t.g.setAttribute("aria-pressed", String(S.selected === a.agent_id));
  }
  // message edges (aggregated by pair) – solid
  const counts = new Map();
  for (const m of s.messages) { if (POS[m.sender] && POS[m.recipient]) counts.set(m.sender + ">" + m.recipient, (counts.get(m.sender + ">" + m.recipient) || 0) + 1); }
  for (const [key, e] of G.edges) if (!counts.has(key)) { e.g.remove(); G.edges.delete(key); }
  for (const [key, n] of counts) {
    const [f, t] = key.split(">"), e = ensureMsgEdge(key, f, t);
    e.label.textContent = n > 1 ? "×" + n : "";
    const [x1, y1] = POS[f], [x2, y2] = POS[t]; e.label.setAttribute("x", ((x1 + x2) / 2 - (y2 - y1) / (Math.hypot(x2 - x1, y2 - y1) || 1) * 17).toFixed(0));
    e.label.setAttribute("y", ((y1 + y2) / 2 + (x2 - x1) / (Math.hypot(x2 - x1, y2 - y1) || 1) * 17).toFixed(0));
  }
  // dependency edges – dashed, never animated
  const want = new Set();
  for (const d of s.dependencies) {
    if (!d.from_agent || !d.to_agent || d.from_agent === d.to_agent) continue;
    const key = d.from_agent + ">" + d.to_agent; want.add(key);
    if (!G.deps.has(key)) { const p = sv("path", {class: "edge-dep", d: edgePath(d.from_agent, d.to_agent, -34), "marker-end": "url(#arrd)", "data-dep": key}); G.gDep.append(p); G.deps.set(key, p); }
  }
  for (const [k, p] of G.deps) if (!want.has(k)) { p.remove(); G.deps.delete(k); }
}
function selectAgent(id) { S.selected = id; updateGraph(); renderPanel(); }

/* ---------------- side panel ---------------- */
function list(items, empty, fn) { return items.length ? h("ul", {}, items.map(fn)) : h("div", {class: "empty"}, empty); }
function demoTag(e) { return e.source === "demo" ? h("span", {class: "tag-demo"}, "DEMO") : null; }
function artifactLink(a) {
  const u = a.url || a.path;
  if (a.url && /^https?:\/\//.test(a.url)) return h("a", {href: a.url, target: "_blank", rel: "noopener noreferrer"}, a.title || a.url);
  const m = /^(?:research\/)?docs\/([A-Za-z0-9_.\-]+\.md)$/.exec(a.path || "");
  if (m) return h("a", {href: "#library", onclick: () => { S.libOpen = m[1]; }}, a.title || a.path);
  return h("span", {}, a.title ? a.title + " " : "", h("code", {}, u));
}
function renderPanel() {
  const p = $("#panel"); if (!p) return; p.textContent = "";
  const s = S.snap;
  if (!S.selected || !s) { p.append(h("h2", {}, "Agent details"), h("div", {class: "empty"}, "Select a tower (click, or focus + Enter) to see its observable messages, tool activity, sources, artifacts, test results and decision summaries."), notice()); return; }
  const a = s.agents.find(x => x.agent_id === S.selected), id = a.agent_id, ev = visibleEvents();
  const mine = ev.filter(e => e.agent_id === id);
  const ref = S.rp.active ? Date.parse(s.reference_time) : Date.now();
  p.append(h("h2", {}, `${ROLE_NAMES[id]}${a.demo ? " " : ""}`, a.demo ? h("span", {class: "tag-demo"}, "DEMO") : null),
    h("div", {class: "muted"}, a.role),
    h("div", {}, h("strong", {}, `${ST_ICON[a.status || "none"]} ${ST_LABEL(a.status)}`), a.status_since ? ` for ${dur((ref - Date.parse(a.status_since)) / 1000)}` : ""),
    h("div", {}, "Task: ", a.task || "–"), h("div", {}, `Completed tasks: ${a.completed_count} · last activity ${tsHms(a.last_activity)}`),
    h("div", {}, "Blockers: ", a.blockers.length ? a.blockers.join("; ") : "none reported"),
    h("div", {}, "Usage: ", a.usage ? `${JSON.stringify(a.usage)} (${a.usage_note})` : "not reported (no usage/cost is estimated)"),
    notice());
  const msgs = ev.filter(e => e.event_type === "message.sent" && (e.payload.sender === id || e.payload.recipient === id));
  p.append(h("h3", {}, "Messages"), list(msgs.slice(-12), "none recorded", e => h("li", {}, `${tsHms(e.timestamp_utc)} ${e.payload.kind === "delegation" ? "delegation " : ""}${ROLE_NAMES[e.payload.sender] || e.payload.sender} → ${ROLE_NAMES[e.payload.recipient] || e.payload.recipient}: ${e.payload.preview}`, demoTag(e))));
  const tools = mine.filter(e => e.event_type === "tool.activity");
  p.append(h("h3", {}, "Tool activity"), list(tools.slice(-12), "none recorded", e => h("li", {}, `${tsHms(e.timestamp_utc)} ${e.payload.tool}: ${e.payload.summary}`, demoTag(e))));
  const srcs = [...new Set(mine.flatMap(e => (e.payload.sources || []).map(String)))];
  p.append(h("h3", {}, "Sources"), list(srcs, "none recorded", x => h("li", {}, h("code", {}, x))));
  const arts = s.artifacts.filter(x => x.agent_id === id && (!S.rp.active || x.seq <= S.rp.cursorSeq));
  p.append(h("h3", {}, "Artifacts"), list(arts, "none recorded", x => h("li", {}, artifactLink(x), x.demo ? h("span", {class: "tag-demo"}, "DEMO") : null)));
  const tests = mine.filter(e => e.event_type === "test.result");
  p.append(h("h3", {}, "Test results"), list(tests, "none recorded", e => h("li", {}, `${e.payload.outcome.toUpperCase()} · ${e.payload.name}${e.payload.detail ? " – " + e.payload.detail : ""}`, demoTag(e))));
  const dec = mine.filter(e => e.event_type === "decision.summary");
  p.append(h("h3", {}, "Decision summaries"), list(dec, "none recorded", e => h("li", {}, `${tsHms(e.timestamp_utc)} ${e.payload.summary}`, demoTag(e))));
}
function notice() { return h("div", {class: "notice"}, "Private reasoning is not shown. Only observable events (messages, tool activity, artifacts, test results, concise decision summaries) are listed, and only if an agent emitted them."); }

/* ---------------- tabs ---------------- */
const TABS = [["feed", "Activity feed"], ["board", "Task board"], ["deps", "Dependencies"], ["search", "Search"], ["controls", "Controls & approvals"]];
function evText(e) {
  const p = e.payload;
  switch (e.event_type) {
    case "message.sent": return `${p.kind === "delegation" ? "delegation" : "message"} ${p.sender} → ${p.recipient}: ${p.preview}`;
    case "agent.status": return `${e.agent_id} is ${p.status}${p.task ? " – " + p.task : ""}`;
    case "task.created": return `task ${p.task_id} created: ${p.title} (${p.owner || "unassigned"})`;
    case "task.updated": return `task ${p.task_id} → ${p.status || "updated"}`;
    case "task.dependency": return `task ${p.task_id} depends on ${p.depends_on}`;
    case "tool.activity": return `${e.agent_id} used ${p.tool}: ${p.summary}`;
    case "artifact.created": return `artifact: ${p.path}`;
    case "test.result": return `test ${p.outcome.toUpperCase()}: ${p.name}`;
    case "decision.summary": return `decision: ${p.summary}`;
    case "incident": return `incident [${p.severity}]: ${p.summary}`;
    case "mode.changed": return `mode → ${p.mode}`;
    case "approval.requested": return `approval requested ${p.approval_id}: ${p.summary}`;
    case "approval.resolved": return `approval ${p.approval_id} ${p.resolution}`;
    case "command.recorded": return `command ${p.command}: ${p.message}`;
    case "heartbeat": return `heartbeat ${e.agent_id}`;
    default: return JSON.stringify(p);
  }
}
function evRow(e) { return h("div", {class: "row" + (S.newSeqs.has(e.seq) ? " new" : "")}, h("span", {class: "mono muted"}, tsHms(e.timestamp_utc)), h("span", {class: "mono"}, e.event_type), h("span", {}, evText(e), demoTag(e))); }
function renderTabs(feedOnly) {
  const box = $("#tabbody"); if (!box) return;
  if (feedOnly && S.tab !== "feed") return;
  const s = S.snap; const prevScroll = box.firstChild && box.firstChild.scrollTop; const atBottom = box.firstChild && box.firstChild.scrollHeight - box.firstChild.scrollTop - box.firstChild.clientHeight < 30;
  box.textContent = "";
  if (!s) { box.append(h("div", {class: "empty"}, S.loading ? "Loading…" : "No data")); return; }
  if (S.tab === "feed") {
    const ev = visibleEvents().slice(-300);
    const f = h("div", {class: "feed", tabindex: "0", "aria-label": "Chronological activity feed"}, ev.length ? ev.map(evRow) : h("div", {class: "empty"}, "No events in this run yet. Emit one with command_center/emit.py."));
    box.append(f); f.scrollTop = (atBottom || prevScroll == null) ? f.scrollHeight : prevScroll;
  } else if (S.tab === "board") {
    box.append(h("div", {class: "board"}, s.task_statuses.map(st => {
      const ts = s.tasks.filter(t => t.status === st);
      return h("div", {class: "col"}, h("h4", {}, `${ST_ICON[st]} ${st.replace(/_/g, " ")} (${ts.length})`),
        ts.length ? ts.map(t => h("div", {class: "tcard"}, h("strong", {}, t.task_id), " ", t.title, h("div", {class: "muted"}, `owner: ${t.owner || "–"}${t.unmet.length ? " · waiting on " + t.unmet.join(", ") : ""}`), t.demo ? h("span", {class: "tag-demo"}, "DEMO") : null)) : h("div", {class: "empty"}, "—"));
    })));
    if (!s.tasks.length) box.append(h("div", {class: "empty"}, "No tasks recorded in this run."));
  } else if (S.tab === "deps") {
    box.append(s.dependencies.length ? h("table", {}, h("thead", {}, h("tr", {}, ...["Task", "depends on", "owners", "state"].map(x => h("th", {}, x)))),
      h("tbody", {}, s.dependencies.map(d => { const t = s.tasks.find(x => x.task_id === d.task_id), u = s.tasks.find(x => x.task_id === d.depends_on);
        return h("tr", {}, h("td", {}, d.task_id, t ? " " + t.title : ""), h("td", {}, d.depends_on, u ? " " + u.title : ""), h("td", {}, `${d.to_agent || "?"} ← ${d.from_agent || "?"}`), h("td", {}, u ? (u.status === "complete" ? "✓ satisfied" : "⧗ unmet (" + u.status + ")") : "unknown task")); }))) : h("div", {class: "empty"}, "No dependencies recorded. (Dependencies are task.dependency events, distinct from messages.)"));
  } else if (S.tab === "search") {
    const inp = h("input", {type: "search", id: "q", placeholder: "search event history…", "aria-label": "Search events", value: S.q || ""}), all = h("label", {class: "inline"}, h("input", {type: "checkbox", id: "allruns"}), "all runs");
    const go = async () => { S.q = inp.value; if (!S.q.trim()) return; try { S.searchRes = (await api(`/api/search?q=${encodeURIComponent(S.q)}${all.firstChild.checked ? "" : "&run_id=" + encodeURIComponent(S.run)}`)).events; } catch (e) { S.searchRes = {error: e.message}; } renderTabs(); };
    inp.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
    box.append(h("div", {class: "formrow"}, inp, all, h("button", {onclick: go}, "Search")));
    const r = S.searchRes;
    box.append(r == null ? h("div", {class: "empty"}, "Enter a term to search the stored history.") : r.error ? h("div", {class: "err"}, r.error) : r.length ? h("div", {class: "feed"}, r.map(evRow)) : h("div", {class: "empty"}, "No matches."));
  } else if (S.tab === "controls") renderControls(box, s);
}
function renderControls(box, s) {
  const agentSel = () => h("select", {"aria-label": "agent"}, ROLE_IDS.map(i => h("option", {value: i}, ROLE_NAMES[i])));
  const res = h("div", {class: "result", role: "status"}, S.cmdResult || "Controls only RECORD a request as an event. No agent runtime is attached, so nothing is executed.");
  const send = async (command, args) => {
    const tok = $("#token").value; if (!tok) { res.textContent = "Token required: enter the X-CC-Token printed when the server started (or in command_center/.cc_token)."; res.className = "result err"; return; }
    try { const r = await api("/api/commands", {method: "POST", headers: {"Content-Type": "application/json", "X-CC-Token": tok}, body: JSON.stringify({command, args, run_id: S.run})}); S.cmdResult = `${command}: ${r.message}`; res.className = "result"; }
    catch (e) { S.cmdResult = `${command}: ${e.status === 401 ? "refused – missing/invalid token" : e.message}`; res.className = "result err"; }
    res.textContent = S.cmdResult; scheduleSnapshot();
  };
  const a1 = agentSel(), title = h("input", {placeholder: "task title", "aria-label": "task title"}), a2 = agentSel(), reason = h("input", {placeholder: "reason (optional)", "aria-label": "reason"});
  const conc = h("input", {type: "number", min: 1, max: s.concurrency.budget_max, value: s.concurrency.recorded || 1, "aria-label": "concurrency"});
  box.append(h("div", {class: "card"},
    h("div", {class: "formrow"}, h("strong", {}, "Assign task"), a1, title, h("button", {onclick: () => send("assign_task", {agent_id: a1.value, title: title.value})}, "Record assignment")),
    h("div", {class: "formrow"}, h("strong", {}, "Request update"), a2, h("button", {onclick: () => send("request_update", {agent_id: a2.value})}, "Record request")),
    h("div", {class: "formrow"}, h("strong", {}, "Cancel research"), reason, h("button", {onclick: () => send("cancel_research", {reason: reason.value})}, "Record cancel request"), h("span", {class: "muted"}, "Never touches positions or protective orders.")),
    h("div", {class: "formrow"}, h("strong", {}, "Concurrency"), conc, h("span", {class: "muted"}, `allowed 1..${s.concurrency.budget_max} (budget) · recorded: ${s.concurrency.recorded ?? "none"} · not enforced`), h("button", {onclick: () => send("set_concurrency", {value: parseInt(conc.value, 10)})}, "Record limit")),
    res));
  const pend = s.approvals.filter(a => !a.resolution);
  box.append(h("h3", {}, "Approvals"), pend.length ? pend.map(a => h("div", {class: "card formrow"}, h("span", {}, h("strong", {}, a.approval_id), " ", a.summary, a.demo ? h("span", {class: "tag-demo"}, "DEMO") : null),
    h("button", {onclick: () => send("approve_proposal", {approval_id: a.approval_id, resolution: "approved"})}, "Record approve"), h("button", {onclick: () => send("approve_proposal", {approval_id: a.approval_id, resolution: "rejected"})}, "Record reject"))) : h("div", {class: "empty"}, "No pending approvals."),
    h("div", {class: "muted"}, "Approving records the decision only; nothing is executed."));
}

/* ---------------- replay ---------------- */
function replayBar() {
  const r = S.rp, total = S.events.length;
  const bar = h("div", {class: "card formrow", id: "replaybar"}, h("strong", {}, "Replay"),
    !r.active ? h("button", {onclick: startReplay, disabled: !total}, "Replay this run") : [
      h("button", {onclick: () => { r.playing = !r.playing; renderView(); }}, r.playing ? "Pause" : "Play"),
      h("label", {class: "inline"}, "Speed", h("select", {onchange: e => r.speed = parseFloat(e.target.value), "aria-label": "replay speed"}, [0.5, 1, 2, 4, 8].map(x => h("option", {value: x, selected: x === r.speed}, x + "×")))),
      h("input", {type: "range", min: 0, max: total, value: r.idx, id: "scrub", "aria-label": "replay position", oninput: e => seekReplay(parseInt(e.target.value, 10))}),
      h("span", {class: "mono"}, `${r.idx}/${total} events`), h("button", {onclick: () => stopReplay(false)}, "Exit replay")],
    h("span", {class: "muted"}, "Replay uses the original event timestamps (gaps over 3s are compressed); speed is client-side."));
  return bar;
}
function startReplay() {
  const r = S.rp; if (!S.events.length) return;
  r.active = true; r.playing = true; r.idx = 0; r.cursorSeq = 0; r.virt = 0; S.pulses.clear();
  const t0 = Date.parse(S.events[0].timestamp_utc); let acc = 0, prev = t0; r.sched = [];
  for (const e of S.events) { const t = Date.parse(e.timestamp_utc); acc += Math.min(3000, Math.max(0, t - prev)); prev = t; r.sched.push(acc); }
  clearInterval(r.timer); r.timer = setInterval(tickReplay, 100);
  loadSnapshot(); renderView(); renderHeader();
}
function tickReplay() {
  const r = S.rp; if (!r.playing) return;
  r.virt += 100 * r.speed; let moved = false;
  while (r.idx < S.events.length && r.sched[r.idx] <= r.virt) { const e = S.events[r.idx++]; r.cursorSeq = e.seq; moved = true; if (e.event_type === "message.sent") pulse(e.payload.sender, e.payload.recipient, e.payload.kind); }
  if (r.idx >= S.events.length) { r.playing = false; renderView(); }
  if (moved) { const sc = $("#scrub"); if (sc) sc.value = r.idx; scheduleSnapshot(); renderTabs(); }
}
function seekReplay(i) { const r = S.rp; r.idx = i; r.cursorSeq = i ? S.events[i - 1].seq : 0; r.virt = i ? r.sched[i - 1] : 0; r.playing = false; scheduleSnapshot(); renderTabs(); }
function stopReplay(silent) {
  const r = S.rp; clearInterval(r.timer); if (!r.active) return;
  r.active = false; r.playing = false; r.cursorSeq = null; if (!silent) { scheduleSnapshot(); renderView(); renderHeader(); }
}

/* ---------------- views ---------------- */
async function setMode(m) {
  const tok = $("#token").value; if (!tok) return cmdNote("Token required to record a mode change; enter X-CC-Token in the header.");
  try { const r = await api("/api/commands", {method: "POST", headers: {"Content-Type": "application/json", "X-CC-Token": tok}, body: JSON.stringify({command: "set_mode", args: {mode: m}, run_id: S.run})}); cmdNote(r.message); }
  catch (e) { cmdNote(e.message); }
}
function renderAll() { renderHeader(); renderView(); }
function renderView() {
  const v = $("#view");
  if (S.view !== "overview") { G = null; }
  if (S.error && !S.snap && S.view === "overview") { v.textContent = ""; v.append(h("div", {class: "card err", role: "alert"}, S.error, " Retrying…")); return; }
  const fn = {overview: viewOverview, library: viewLibrary, backtest: viewBacktest, audit: viewAudit}[S.view];
  if (fn) return fn(v);
  const [, name] = NAV.find(n => n[0] === S.view);
  v.textContent = "";
  v.append(h("h2", {}, name), h("div", {class: "card"}, h("strong", {}, "Not implemented in this slice: "), PREREQ[S.view]),
    S.view === "execution" ? h("div", {class: "formrow"}, h("button", {"aria-disabled": "true", onclick: () => cmdNote("PAPER: blocked: requires approval & broker adapter")}, "Enable PAPER (blocked)"), h("button", {"aria-disabled": "true", onclick: () => cmdNote("LIVE: blocked: requires approval & broker adapter")}, "Enable LIVE (blocked)"), h("span", {id: "mode-note", class: "note"}, "blocked: requires approval & broker adapter")) : null);
}
function viewOverview(v) {
  if (!document.getElementById("graph-wrap")) {
    v.textContent = "";
    if (!G) initGraph();
    v.append(h("div", {class: "ov"}, h("div", {}, G.wrap, h("div", {id: "replay-slot"})), h("aside", {class: "card", id: "panel", "aria-live": "off"})),
      h("div", {class: "tabs", role: "tablist"}), h("div", {id: "tabbody"}));
    fitGraph();
  }
  const slot = $("#replay-slot"); slot.textContent = ""; slot.append(replayBar());
  const tabs = v.querySelector(".tabs"); tabs.textContent = "";
  for (const [id, name] of TABS) tabs.append(h("button", {role: "tab", "aria-selected": String(S.tab === id), onclick: () => { S.tab = id; renderView(); }}, name));
  if (S.loading && !S.snap) { $("#panel").textContent = "Loading…"; }
  else if (S.snap && !S.snap.event_count) { $("#tabbody").textContent = ""; renderPanel(); $("#tabbody").append(h("div", {class: "empty"}, "Empty: no events in this run. Seed labelled demo data with command_center/seed_demo.py or post real events with command_center/emit.py.")); updateGraph(); return; }
  updateGraph(); renderPanel(); renderTabs();
}
async function mdIndex(v, title, url, intro, empty) {
  v.textContent = ""; v.append(h("h2", {}, title), h("div", {class: "muted"}, intro));
  let r; try { r = await api(url); } catch (e) { v.append(h("div", {class: "err"}, "Error: " + e.message)); return; }
  const viewer = h("div", {id: "mdview"});
  v.append(r.items.length ? h("table", {}, h("thead", {}, h("tr", {}, ...["Title", "File", "Bytes", "Modified (UTC)"].map(x => h("th", {}, x)))),
    h("tbody", {}, r.items.map(it => h("tr", {}, h("td", {}, h("a", {href: "#", onclick: async ev => { ev.preventDefault(); viewer.textContent = "Loading…"; try { const t = await (await fetch(`${url}/${encodeURIComponent(it.name)}`)).text(); viewer.textContent = ""; viewer.append(h("h3", {}, it.name), h("pre", {class: "pre"}, t)); } catch (e) { viewer.textContent = "Error: " + e.message; } }}, it.title)),
      h("td", {class: "mono"}, it.name), h("td", {}, it.bytes), h("td", {class: "mono"}, it.modified))))) : h("div", {class: "empty"}, empty), viewer);
  if (S.libOpen && url === "/api/docs") { const n = S.libOpen; S.libOpen = null; const t = await (await fetch(`/api/docs/${n}`)).text(); viewer.append(h("h3", {}, n), h("pre", {class: "pre"}, t)); }
}
function viewLibrary(v) { mdIndex(v, "Research library", "/api/docs", "Read-only index of research/docs/*.md. Titles are the first '# ' heading.", "No docs found."); }
function viewBacktest(v) {
  mdIndex(v, "Backtest lab", "/api/results", "Lists results/daily/*.md if present (read-only). Running or configuring backtests from this UI is not implemented in this slice: it requires a backtest job runner that emits events; use the CLI under src/tradelab.", "No results/daily/*.md files exist yet. Nothing is shown rather than inventing results.");
}
async function viewAudit(v) {
  v.textContent = ""; v.append(h("h2", {}, "Audit / settings"));
  const s = S.snap;
  v.append(h("div", {class: "card"}, h("strong", {}, "Not implemented in this slice: "), "a full audit trail (e.g. results/test_access_log.jsonl viewer, config diffs, approval history across runs). Implemented below: the recorded command log and store info for the selected run."));
  v.append(h("h3", {}, "Settings"), h("div", {class: "card"}, s ? `Concurrency budget max: ${s.concurrency.budget_max} · recorded limit: ${s.concurrency.recorded ?? "none"} (not enforced; no runtime attached). Stale threshold: ${S.staleMs / 1000}s. Runtime attached: no.` : "No snapshot."),
    h("div", {class: "muted"}, "The command token is entered in the header (kept in sessionStorage of this tab only)."));
  v.append(h("h3", {}, "Recorded commands (this run)"));
  v.append(s && s.commands.length ? h("table", {}, h("thead", {}, h("tr", {}, ...["Time", "Command", "Accepted", "Result"].map(x => h("th", {}, x)))), h("tbody", {}, s.commands.map(c => h("tr", {}, h("td", {class: "mono"}, tsHms(c.timestamp_utc)), h("td", {}, c.command), h("td", {}, c.accepted ? "yes" : "REFUSED"), h("td", {}, c.message))))) : h("div", {class: "empty"}, "No commands recorded."));
}

/* ---------------- boot ---------------- */
document.addEventListener("DOMContentLoaded", () => {
  const tok = $("#token"); tok.value = ss.get("cc.token") || ""; tok.addEventListener("input", () => ss.set("cc.token", tok.value));
  const th = $("#theme"), setTheme = t => { document.documentElement.dataset.theme = t; th.textContent = "Theme: " + t; th.setAttribute("aria-label", `Theme: ${t}. Click to change`); ls.set("cc.theme", t); };
  setTheme(document.documentElement.dataset.theme);
  th.addEventListener("click", () => setTheme({dark: "light", light: "system", system: "dark"}[document.documentElement.dataset.theme] || "dark"));
  $("#run-select").addEventListener("change", e => loadRun(e.target.value));
  renderNav(); renderHeader(); route(); init();
  setInterval(async () => { if (S.conn === "stale" || S.rp.active) return; try { const r = await api("/api/runs"); const sig = JSON.stringify(r.runs.map(x => [x.run_id, x.n])); if (sig !== S.runSig) { S.runSig = sig; const keep = S.run; await loadRuns(); $("#run-select").value = keep; } } catch (e) {} }, 8000);
});
window.__cc = S;
