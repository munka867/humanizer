// Tower graph: DOM + SVG. Fixed layout (see layout.js), zoom/pan/fit/centre, keyboard navigation, connector pulse.
// A hierarchy line is structure only. A message connector exists ONLY for an observed message.sent event; it pulses ONLY when such an event
// arrives live or is replayed. A dependency connector exists ONLY for task.dependency events whose two tasks have known, different owners.
import { AGENT_IDS, SPECIALISTS, ROLE, displayStatus, messagePairs, dependencyPairs, pairKey, elapsedText, usageRows, tms } from "./model.js";
import { makeLayout, createLaneBook, fitTransform, centerOnTransform, zoomAt, neighbor } from "./layout.js";
import { statusBadge, timeLabel } from "./common.js";

const PULSE_MS = 2600;
export const FULL_MIN_WIDTH = 1120;

export function createGraph(ctx, { onSelect, onEdgeInfo, onNeedHeight } = {}) {
  const { el, ui, fmt } = ctx;
  const svg = (tag, attrs, ...kids) => el("svg:" + tag, attrs, ...kids);
  const uidp = "ag" + Math.random().toString(36).slice(2, 7);
  let L = makeLayout("full"), book = createLaneBook(L), density = "full", compactW = 0;
  let T = { s: 1, x: 0, y: 0 }, userMoved = false, vw = 0, vh = 0;
  let view = null, uis = { selected: null, filters: {}, refMs: Date.now(), mode: "live", hl: null };
  const towers = {}, edges = new Map(), pulses = new Map(), tips = new Map();
  const disposers = [];

  // ---------------------------------------------------------------- skeleton
  const stage = el("div", { class: "ag-stage", dataset: { density }, role: "group", "aria-label": "Agent towers: the coordinator on top and six specialists below" });
  const edgeSvg = svg("svg", { class: "ag-edges", "aria-hidden": "false", focusable: "false" });
  const defs = svg("defs", {}, ...[["msg", "ag-arrow-msg"], ["dep", "ag-arrow-dep"]].map(([k, cls]) =>
    svg("marker", { id: `${uidp}-${k}`, viewBox: "0 0 10 10", refX: 8.5, refY: 5, markerWidth: 8, markerHeight: 8, orient: "auto-start-reverse", markerUnits: "userSpaceOnUse" }, svg("path", { d: "M0 0.5L10 5L0 9.5z", class: cls }))));
  const gHier = svg("g", { class: "ag-g-hier" }), gDep = svg("g", { class: "ag-g-dep" }), gMsg = svg("g", { class: "ag-g-msg" });
  edgeSvg.append(defs, gHier, gDep, gMsg);
  const userPill = el("div", { class: "ag-user", title: "Messages from or to the human user appear on the line to the coordinator" }, ctx.ui.icon("agents", 16), el("span", {}, "You"));
  stage.append(edgeSvg, userPill);

  const viewport = el("div", { class: "ag-viewport", tabindex: -1 }, stage);
  const zoomLabel = el("span", { class: "ag-zoom-label", "aria-live": "off" }, "100%");
  const ctl = (label, title, fn) => ui.btn(label, { onClick: fn, title, ariaLabel: title });
  const controls = el("div", { class: "ag-ctl", role: "toolbar", "aria-label": "Graph view controls" },
    ctl("−", "Zoom out", () => zoomBy(1 / 1.2)), zoomLabel, ctl("+", "Zoom in", () => zoomBy(1.2)),
    ctl("Fit", "Fit the whole team to the screen", () => fit(true)), ctl("Centre", "Centre on the active agent", () => api.centerActive()));
  const legendLine = (cls, label) => el("span", { class: "ag-leg-item" }, svg("svg", { width: 34, height: 10, viewBox: "0 0 34 10", "aria-hidden": "true" }, svg("path", { d: "M1 5H33", class: cls })), el("span", {}, label));
  const legend = el("div", { class: "ag-legend", role: "group", "aria-label": "Connector legend" },
    legendLine("ag-line-hier", "Reporting line"), legendLine("ag-line-dep", "Task dependency"), legendLine("ag-line-msg", "Message (pulses when one arrives)"));
  viewport.append(controls);
  const root = el("div", { class: "ag-graph" }, viewport, legend);

  // ---------------------------------------------------------------- towers
  function crownSvg(isLead, full = true) {
    const w = isLead ? 120 : full ? 72 : 56, h = isLead ? 30 : 22;
    return svg("svg", { class: "t-crown", width: w, height: h, viewBox: `0 0 ${w} ${h}`, "aria-hidden": "true" },
      svg("path", { d: `M${w / 2} 1V${isLead ? 12 : 8}`, class: "t-spire" }),
      svg("path", { d: isLead ? `M14 ${h}V19L32 19V11L${w - 32} 11V19L${w - 14} 19V${h}` : `M12 ${h}V12L${w - 12} 12V${h}`, class: "t-roof" }),
      svg("circle", { cx: w / 2, cy: 2.5, r: 2.8, class: "t-beacon" }));
  }
  function buildTower(id) {
    const lead = id === "lead";
    const t = {
      id, root: null,
      title: el("div", { class: "t-title" }, ROLE[id].name),
      demo: el("span", { class: "t-demo", hidden: true }, "DEMO"),
      badgeSlot: el("span", { class: "t-bslot" }),
      badge: null,
      task: el("div", { class: "t-task" }),
      elapsed: el("div", { class: "t-line t-elapsed" }),
      last: el("div", { class: "t-line t-last" }),
      counts: el("div", { class: "t-line t-counts" }),
      hb: el("div", { class: "t-line t-hb" }),
      result: el("div", { class: "t-result" }),
      resultText: null,
    };
    t.badge = el("div", { class: "t-badge" }, t.badgeSlot, t.demo);
    t.resultLabel = el("span", { class: "t-result-k" }, "Latest result:");
    t.resultText = el("span", { class: "t-result-v" });
    t.result.append(t.resultLabel, t.resultText);
    t.root = el("div", { class: ["ag-tower", lead ? "is-lead" : ""], role: "button", tabindex: 0, dataset: { agent: id }, "aria-pressed": "false",
      on: { click: () => onSelect?.(id, "tower"), keydown: (e) => towerKey(e, id) } },
      crownSvg(lead),
      el("div", { class: "t-body" },
        el("div", { class: "t-head" }, t.title), t.badge, t.task,
        el("div", { class: "t-meta" }, t.elapsed, t.last, t.counts, t.hb), t.result));
    stage.append(t.root);
    towers[id] = t;
  }
  AGENT_IDS.forEach(buildTower);

  function towerKey(e, id) {
    const k = e.key;
    if (k === "Enter" || k === " ") { e.preventDefault(); onSelect?.(id, "tower", { keyboard: true }); return; }
    const dir = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" }[k];
    if (!dir) return;
    e.preventDefault();
    const nx = neighbor(id, dir, api.lastSpec);
    if (nx && nx !== id) { if (SPECIALISTS.includes(nx)) api.lastSpec = nx; api.focusTower(nx); }
  }

  function setText(node, v) { if (node.textContent !== v) node.textContent = v; }

  function updateTower(ag, v) {
    const t = towers[ag.id], key = displayStatus(ag), f = uis.filters || {};
    const refMs = uis.refMs, live = uis.mode === "live";
    const dim = (f.role && f.role !== ag.id) || (f.status && f.status !== key);
    t.root.classList.toggle("is-inactive", !ag.has_events);
    t.root.classList.toggle("is-dim", !!dim);
    t.root.dataset.status = key;
    t.root.dataset.active = ag.has_events && ag.status === "working" ? "1" : "0";
    t.root.classList.toggle("is-selected", uis.selected === ag.id);
    t.root.setAttribute("aria-pressed", uis.selected === ag.id ? "true" : "false");
    t.root.classList.toggle("is-hl", !!uis.hl?.agents?.has(ag.id));
    t.demo.hidden = !(ag.demo && !ag.real);
    // status badge (replace only on change)
    if (t.badge.dataset.k !== key) { t.badge.dataset.k = key; t.badgeSlot.replaceChildren(statusBadge(ctx, key)); }
    // current task
    let task;
    if (!ag.has_events) task = "No agent running";
    else if (!ag.status) task = "No task reported";
    else if (ag.task) task = (ag.status === "complete" || ag.status === "idle" ? "Last task: " : "") + ag.task;
    else task = ag.status === "queued" ? "Queued, no task text" : "No task text reported";
    setText(t.task, task); t.task.title = task;
    t.task.classList.toggle("is-empty", !ag.has_events || !ag.status);
    // times
    if (!ag.has_events) {
      setText(t.elapsed, "No events from this agent"); t.elapsed.dataset.since = ""; setText(t.last, ""); t.counts.replaceChildren();
    } else {
      if (ag.status && ag.status_since) {
        t.elapsed.dataset.since = ag.status_since; setText(t.elapsed, `In status ${elapsedText(ag.status_since, refMs)}`);
        t.elapsed.title = live ? "Time since the last status change (counts up while the stream is live)" : `Measured to ${timeLabel(ctx, new Date(refMs).toISOString(), refMs)} (${uis.mode === "replay" ? "replay time" : uis.mode === "stale" ? "last data received" : "last event of this run"}), not to now`;
      }
      else { t.elapsed.dataset.since = ""; setText(t.elapsed, "No status event yet"); }
      setText(t.last, `Last ${timeLabel(ctx, ag.last_activity, refMs)}`); t.last.title = `Last event ${timeLabel(ctx, ag.last_activity, refMs)}`;
      setText(t.counts, `Queued ${ag.queued} · Done ${ag.completed}`);
    }
    // heartbeat is shown separately and never as a work status
    const hbTxt = ag.last_heartbeat ? "Service reachable" : "";
    setText(t.hb, hbTxt); t.hb.hidden = !hbTxt; t.hb.title = ag.last_heartbeat ? `Heartbeat ${timeLabel(ctx, ag.last_heartbeat, refMs)}. A heartbeat only shows the service is reachable; it is not a work status.` : "";
    // latest result preview from real decision/test/artifact events
    const lr = ag.latest;
    t.result.classList.toggle("is-empty", !lr);
    if (lr) { setText(t.resultLabel, lr.kind === "decision" ? "Decision:" : lr.kind === "test" ? "Test:" : "Artifact:"); setText(t.resultText, lr.text); t.result.title = lr.text; }
    else { setText(t.resultLabel, "Latest result"); setText(t.resultText, ag.has_events ? "none reported" : "—"); t.result.title = ""; }
    const art = uis.hl?.artifact && uis.hl.artifact.agent === ag.id ? uis.hl.artifact : null;   // a selected artifact event is surfaced on its tower
    if (art) { setText(t.resultLabel, "Artifact:"); setText(t.resultText, art.path); t.result.title = art.path; t.result.classList.remove("is-empty"); }
    t.root.classList.toggle("is-art-hl", !!art);
    const label = `${ROLE[ag.id].name} (${ag.id}). ${ag.has_events ? (STATUS_LABEL(key)) : "Not started, no agent running"}. ${task}. ${ag.has_events ? `Last event ${timeLabel(ctx, ag.last_activity, refMs)}. ${ag.queued} queued, ${ag.completed} completed.` : ""}`;
    t.root.setAttribute("aria-label", label);
  }
  const STATUS_LABEL = (k) => ({ queued: "Queued", working: "Working", awaiting_dependency: "Waiting on dependency", awaiting_approval: "Awaiting approval", idle: "Idle", failed: "Failed", complete: "Complete", no_status: "No status reported" }[k] || k);

  // ---------------------------------------------------------------- layout application
  function applyLayout() {
    stage.dataset.density = density;
    stage.style.width = L.size.w + "px"; stage.style.height = L.size.h + "px";
    edgeSvg.setAttribute("width", L.size.w); edgeSvg.setAttribute("height", L.size.h); edgeSvg.setAttribute("viewBox", `0 0 ${L.size.w} ${L.size.h}`);
    for (const id of AGENT_IDS) { const n = L.nodes[id], r = towers[id].root; r.style.left = n.x + "px"; r.style.top = n.y + "px"; r.style.width = n.w + "px"; r.style.height = n.h + "px"; }
    userPill.style.left = L.user.x + "px"; userPill.style.top = L.user.y + "px"; userPill.style.width = L.user.w + "px"; userPill.style.height = L.user.h + "px";
    // hierarchy lines are static
    gHier.replaceChildren();
    for (const r of Object.values(L.routes.hier)) gHier.append(edgeGroup("hier", r.id, r, `Reporting line: coordinator to ${ROLE[r.id].name}. Structure only, not communication.`));
    for (const e of edges.values()) { e.g.remove(); } edges.clear(); tips.forEach((t) => t.destroy()); tips.clear();
    book = createLaneBook(L);
  }
  function edgeGroup(kind, key, route, label, { focusable = false } = {}) {
    const line = svg("path", { class: ["ag-line", `ag-line-${kind}`], d: route.d, fill: "none" });
    const glow = svg("path", { class: "ag-pulse", d: route.d, fill: "none" });
    const hit = svg("path", { class: "ag-hit", d: route.d, fill: "none", tabindex: focusable ? 0 : -1, role: "button", "aria-label": label });
    const g = svg("g", { class: ["ag-edge", `ag-edge-${kind}`], dataset: { kind, pair: key } }, line, glow, hit);
    return g;
  }

  function routeFor(kind, a, b) {
    const k = pairKey(a, b);
    if (a === "user" || b === "user") return kind === "msg" && k === pairKey("lead", "user") ? L.routes.msg[k] : null;
    if (a === "lead" || b === "lead") return L.routes[kind][k] || null;
    return book.assign(kind === "msg" ? "m" : "d", a, b);
  }

  function ensureEdge(kind, grp) {
    const id = `${kind}:${grp.key}`;
    const route = routeFor(kind, grp.a, grp.b); if (!route) return null;
    let e = edges.get(id);
    if (!e) {
      const g = edgeGroup(kind, grp.key, route, "", { focusable: true });
      (kind === "msg" ? gMsg : gDep).append(g);
      const hit = g.querySelector(".ag-hit");
      hit.addEventListener("click", () => onEdgeInfo?.(kind, edges.get(id)?.grp, hit));
      hit.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); onEdgeInfo?.(kind, edges.get(id)?.grp, hit); } });
      tips.set(id, ui.tooltip(hit, () => edgeTip(kind, edges.get(id)?.grp), { delay: 150 }));
      e = { id, kind, g, hit, line: g.querySelector(".ag-line"), route, grp };
      edges.set(id, e);
    }
    e.grp = grp;
    return e;
  }
  function edgeTip(kind, grp) {
    if (!grp) return "";
    if (kind === "msg") { const m = grp.last; return `${m.sender} to ${m.recipient}, ${timeLabel(ctx, m.ts, uis.refMs)}: ${m.preview}${grp.count > 1 ? ` (${grp.count} messages)` : ""}`; }
    return grp.items.map((d) => `${d.task_id} waits on ${d.depends_on} (${d.from_agent} to ${d.to_agent})`).join("; ");
  }

  function updateEdges(v) {
    const seen = new Set();
    for (const grp of messagePairs(v.messages).values()) {
      const e = ensureEdge("msg", grp); if (!e) continue; seen.add(e.id);
      const senderIsRouteA = grp.last.sender === e.route.a;
      e.line.setAttribute("marker-end", senderIsRouteA ? `url(#${uidp}-msg)` : "");
      e.line.setAttribute("marker-start", senderIsRouteA ? "" : `url(#${uidp}-msg)`);
      e.hit.setAttribute("aria-label", `Messages between ${grp.a} and ${grp.b}: ${grp.count}. Latest from ${grp.last.sender} to ${grp.last.recipient}: ${grp.last.preview}. Press Enter for details.`);
      e.g.dataset.count = String(grp.count);
    }
    for (const grp of dependencyPairs(v.deps).values()) {
      const e = ensureEdge("dep", grp); if (!e) continue; seen.add(e.id);
      const fwd = grp.items.some((d) => d.from_agent === e.route.a), back = grp.items.some((d) => d.from_agent === e.route.b);
      e.line.setAttribute("marker-end", fwd ? `url(#${uidp}-dep)` : ""); e.line.setAttribute("marker-start", back ? `url(#${uidp}-dep)` : "");
      e.hit.setAttribute("aria-label", `Task dependency between ${grp.a} and ${grp.b}: ${grp.items.length} (${grp.items.map((d) => `${d.task_id} waits on ${d.depends_on}`).join("; ")}). Press Enter for details.`);
      e.g.dataset.count = String(grp.items.length);
    }
    for (const [id, e] of edges) if (!seen.has(id)) { e.g.remove(); tips.get(id)?.destroy(); tips.delete(id); edges.delete(id); pulses.delete(id); }
    const hp = uis.hl?.pair;
    for (const e of edges.values()) e.g.classList.toggle("is-hl", e.kind === "msg" && hp === e.grp.key);
    // dim edges when a role filter is active and neither end matches
    const f = uis.filters || {};
    for (const e of edges.values()) e.g.classList.toggle("is-dim", !!(f.role && e.grp.a !== f.role && e.grp.b !== f.role));
  }

  // ---------------------------------------------------------------- pulse (REAL message.sent only; caller guarantees)
  function pulse(a, b) {
    const e = edges.get(`msg:${pairKey(a, b)}`); if (!e) return false;
    clearTimeout(pulses.get(e.id));
    e.g.dataset.pulse = "1"; e.g.classList.remove("is-pulsing"); void e.g.getBoundingClientRect(); e.g.classList.add("is-pulsing");
    pulses.set(e.id, setTimeout(() => { delete e.g.dataset.pulse; e.g.classList.remove("is-pulsing"); pulses.delete(e.id); }, PULSE_MS));
    return true;
  }

  // ---------------------------------------------------------------- view transform
  function applyT() {
    stage.style.transform = `translate(${T.x}px, ${T.y}px) scale(${T.s})`;
    zoomLabel.textContent = Math.round(T.s * 100) + "%";
    stage.style.setProperty("--ag-scale", String(T.s));
  }
  function measure() { const r = viewport.getBoundingClientRect(); vw = r.width; vh = r.height; }
  function fit(force) {
    measure(); if (!vw || !vh) return;
    if (force) userMoved = false;
    T = fitTransform(L.size, vw, vh, { min: 0.8, max: 1 }); applyT();
  }
  function zoomBy(f) { measure(); userMoved = true; T = zoomAt(T, f, vw / 2, vh / 2); applyT(); }
  function wantedDensity() { const r = viewport.getBoundingClientRect(); return r.width >= FULL_MIN_WIDTH ? "full" : "compact"; }
  function relayout() {
    measure(); if (!vw) return;
    const d = wantedDensity(), cw = d === "compact" ? Math.max(126, Math.min(140, Math.floor((vw - 24 - 40 - 50) / 6))) : 0;
    if (d !== density || cw !== compactW) { density = d; compactW = cw; L = makeLayout(d, d === "compact" ? vw : 0); applyLayout(); if (view) { updateEdges(view); } userMoved = false; }
    // height needed so the fit scale is limited by WIDTH (keeps text >= ~13px effective) instead of by a short pane
    onNeedHeight?.(Math.ceil(L.size.h * Math.min(1, Math.max(0.8, (vw - 24) / L.size.w))) + 24 + legend.offsetHeight);
    if (!userMoved) fit(false);
  }
  const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => relayout()) : null;
  ro?.observe(viewport);

  // pan (pointer drag on empty space) + ctrl/cmd wheel zoom
  let pan = null;
  viewport.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || e.target.closest(".ag-tower, .ag-ctl, .ag-hit, .ag-legend, .ag-user")) return;
    pan = { x: e.clientX, y: e.clientY, tx: T.x, ty: T.y }; viewport.setPointerCapture(e.pointerId); viewport.classList.add("is-panning");
  });
  viewport.addEventListener("pointermove", (e) => { if (!pan) return; userMoved = true; T = { ...T, x: pan.tx + (e.clientX - pan.x), y: pan.ty + (e.clientY - pan.y) }; applyT(); });
  const endPan = () => { pan = null; viewport.classList.remove("is-panning"); };
  viewport.addEventListener("pointerup", endPan); viewport.addEventListener("pointercancel", endPan);
  viewport.addEventListener("wheel", (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault(); const r = viewport.getBoundingClientRect(); userMoved = true;
    T = zoomAt(T, e.deltaY < 0 ? 1.1 : 1 / 1.1, e.clientX - r.left, e.clientY - r.top); applyT();
  }, { passive: false });
  viewport.addEventListener("keydown", (e) => {
    if (e.target.closest(".ag-ctl")) return;
    if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomBy(1.2); } else if (e.key === "-") { e.preventDefault(); zoomBy(1 / 1.2); }
    else if (e.key === "0") { e.preventDefault(); fit(true); }
  });

  // elapsed-time ticker (text only, no layout change): live mode measures to now; recorded/replay to the as-of reference
  const ticker = setInterval(() => {
    if (uis.mode !== "live") return;
    const now = Date.now();
    for (const t of Object.values(towers)) { const since = t.elapsed.dataset.since; if (since) setText(t.elapsed, `In status ${elapsedText(since, now)}`); }
  }, 1000);

  applyLayout();

  const api = {
    el: root, lastSpec: "strategy_researcher",
    update(v, u) { view = v; uis = { ...uis, ...u }; for (const ag of v.agentList) updateTower(ag, v); updateEdges(v); },
    pulse, fit, zoomBy, relayout,
    focusTower(id) { towers[id]?.root.focus({ preventScroll: false }); },
    centerOn(id) { measure(); const n = L.nodes[id]; if (!n) return; userMoved = true; T = centerOnTransform(n, T, vw, vh); applyT(); },
    centerActive() {
      let id = uis.selected;
      if (!id && view) {
        const working = view.agentList.filter((a) => a.has_events && a.status === "working").sort((p, q) => (q.last_activity_seq || 0) - (p.last_activity_seq || 0));
        const recent = view.agentList.filter((a) => a.has_events).sort((p, q) => (q.last_activity_seq || 0) - (p.last_activity_seq || 0));
        id = (working[0] || recent[0] || { id: "lead" }).id;
      }
      api.centerOn(id || "lead"); return id || "lead";
    },
    resetEdges() { for (const e of edges.values()) e.g.remove(); edges.clear(); tips.forEach((t) => t.destroy()); tips.clear(); pulses.forEach(clearTimeout); pulses.clear(); book.reset(); },
    towerEl: (id) => towers[id]?.root,
    edgeEl: (kind, a, b) => edges.get(`${kind}:${pairKey(a, b)}`)?.g || null,
    transform: () => ({ ...T, density }),
    layout: () => L,
    destroy() { clearInterval(ticker); ro?.disconnect(); pulses.forEach(clearTimeout); tips.forEach((t) => t.destroy()); disposers.forEach((f) => f()); },
  };
  void tms; void usageRows; void fmt;
  return api;
}
