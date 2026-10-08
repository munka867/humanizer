// Agent Team workspace (AGENTS worker). Tower graph + inspector + synchronized timeline/task board/dependencies + replay + demo.
// Real mode derives ONLY from backend events (/api/events, /api/stream via ctx.events). Demo data lives in a separate client-side state.
// No order paths, no broker imports, no innerHTML: every external string reaches the DOM as a text node through ctx.el.
import { AGENT_IDS, ROLE, STATUS_META, EVENT_TYPES, derive, eventAgents, summarizeEvent, pairKey, displayStatus, tms } from "../agents/model.js";
import { createGraph } from "../agents/graph.js";
import { createPlayer, SPEEDS, gapLabel } from "../agents/replay.js";
import { demoEvents, DEMO_RUN_ID } from "../agents/demo.js";
import { createSummary, createTimeline, createBoard, createDeps, createList } from "../agents/panels.js";
import { createInspector, openDocDrawer } from "../agents/inspector.js";
import { createActions } from "../agents/actions.js";
import { timeLabel, statusBadge, plural } from "../agents/common.js";

const CSS_HREF = "/static/css/agents.css";
const NARROW_PX = 900;

function loadCss() {
  return new Promise((resolve) => {
    let link = document.querySelector("link[data-agents-css]");
    if (link) return resolve(link);
    link = document.createElement("link");
    link.rel = "stylesheet"; link.href = CSS_HREF; link.dataset.agentsCss = "1";
    const done = () => resolve(link);
    link.addEventListener("load", done); link.addEventListener("error", done);
    document.head.append(link); setTimeout(done, 2500);
  });
}

let cleanup = null;

export default {
  id: "agents", title: "Agent Team", icon: "agents",
  async mount(host, ctx) {
    await loadCss();
    const { el, ui, fmt } = ctx;
    const disposers = [];
    const timers = new Set();
    let dead = false;
    const later = (fn, ms) => { const h = setTimeout(() => { timers.delete(h); fn(); }, ms); timers.add(h); return h; };

    // ------------------------------------------------------------------ state
    const S = {
      runs: [], latestRun: null, runId: null, loaded: false, loadError: null, events: [], seqs: new Set(), eventIds: new Set(), buffer: [], loadedMax: 0,
      demo: false, demoEvents: [], replay: null, replayBase: [], replayIdx: 0, replayState: null,
      selected: null, selectedEvent: null, filters: { status: "", role: "", type: "" }, view: null, conn: ctx.events.state(), newRun: null,
      outcome: null, budgetMax: 3, searchSeq: 0, pendingPulses: [], rafId: 0,
    };
    const isNarrow = () => window.innerWidth < NARROW_PX;
    const savedView = ctx.prefs.get("agents.view", null);
    S.view = savedView === "graph" || savedView === "list" ? savedView : (isNarrow() ? "list" : "graph");

    // ------------------------------------------------------------------ derived selectors
    const baseEvents = () => (S.demo ? S.demoEvents : S.events);
    const shownEvents = () => (S.replay ? S.replayBase.slice(0, S.replayIdx) : baseEvents());
    const mode = () => (S.replay ? "replay" : S.demo ? "demo" : (S.runId && S.runId === S.latestRun ? "live" : "recorded"));
    const connOk = () => S.conn.conn === "live";
    const canAct = () => mode() === "live" && !S.demo;
    const actReason = () => S.demo ? "Demo data is client-side only; no command is sent." : S.replay ? "Replay is read-only; nothing is posted." : mode() === "recorded" ? "Select the latest run (live) to record commands." : "";
    let V = derive([]), vKey = "";
    function refMs() {
      const m = mode();
      if (m === "live") return connOk() ? Date.now() : (S.conn.lastBeat || Date.now());
      const evs = shownEvents(); const last = evs[evs.length - 1];
      return last ? tms(last.timestamp_utc) : (S.replayBase[0] ? tms(S.replayBase[0].timestamp_utc) : Date.now());
    }
    const viewMode = () => { const m = mode(); return m === "live" && !connOk() ? "stale" : m; };

    // ------------------------------------------------------------------ DOM skeleton
    const runSel = el("select", { class: "input ag-runsel", "aria-label": "Run", on: { change: () => switchRun(runSel.value) } });
    const modeSlot = el("span", { class: "ag-badges" });
    const searchIn = el("input", { type: "search", class: "input ag-search", placeholder: "Search agents, tasks, events", "aria-label": "Search agents, tasks and events", autocomplete: "off", spellcheck: "false" });
    const searchPop = el("div", { class: "ag-search-pop", role: "listbox", "aria-label": "Search results", hidden: true });
    const filterBtn = ui.btn("Filters", { icon: "list", onClick: () => openFilters(filterBtn), title: "Filter by status, role and event type" });
    filterBtn.setAttribute("aria-haspopup", "dialog");
    const viewSeg = el("div", { class: "ag-seg ag-viewseg", role: "group", "aria-label": "View" });
    const replayBtn = ui.btn("Replay", { icon: "backtest", onClick: () => (S.replay ? exitReplay() : startReplay()), title: "Replay the recorded events with their original timestamps. Posts nothing." });
    const demoBtn = ui.btn("Demo", { icon: "flask", onClick: () => toggleDemo(), title: "Load clearly labelled demo data into a separate client-side state" });
    const startBtn = ui.btn("Start research task", { kind: "primary", icon: "flask", onClick: () => actions.startTask(S.selected || undefined) });
    const toolbar = el("header", { class: "ag-toolbar", role: "toolbar", "aria-label": "Agent team controls" },
      el("div", { class: "ag-tb-l" }, el("label", { class: "ag-runlbl" }, el("span", { class: "sr-only" }, "Run"), runSel), modeSlot),
      el("div", { class: "ag-tb-r" }, el("div", { class: "ag-searchwrap" }, ui.icon("search", 16), searchIn, searchPop), filterBtn, viewSeg, replayBtn, demoBtn, startBtn));
    const banner = el("div", { class: "ag-banner", role: "status", hidden: true });
    const outcome = el("div", { class: "ag-outcome", role: "status", "aria-live": "polite", hidden: true });
    const replayBar = el("div", { class: "ag-replay", role: "group", "aria-label": "Replay controls", hidden: true });

    const graph = createGraph(ctx, { onSelect: (id, src, o) => selectAgent(id, o), onEdgeInfo: (kind, grp, anchor) => edgeInfo(kind, grp, anchor), onNeedHeight: (h) => { graphPane.style.minHeight = h + "px"; } });
    const list = createList(ctx, { onSelectAgent: (id) => selectAgent(id) });
    const graphPane = el("section", { class: "ag-graphpane", "aria-label": "Agent tower graph" }, graph.el);
    const listPane = el("section", { class: "ag-listpane", "aria-label": "Agent list" }, list.el);
    const inspectorAside = el("aside", { class: "ag-inspector", "aria-label": "Agent inspector" });
    const main = el("div", { class: "ag-main" }, graphPane, listPane, inspectorAside);

    const actions = createActions(ctx, { getRunId: () => (mode() === "live" ? S.runId : null), canAct, actReason, onOutcome: (o) => { S.outcome = o; renderOutcome(); } });
    const inspector = createInspector(ctx, { onClose: () => deselect(), actions, openDoc: (p, url) => openDocDrawer(ctx, p, url) });
    const summary = createSummary(ctx, { onSelectEvent: (e) => e && selectEvent(e), onConcurrency: (n) => actions.setConcurrency(n), onApproval: (a, r) => actions.approve(a, r) });
    const timeline = createTimeline(ctx, { onSelectEvent: (e) => selectEvent(e) });
    const board = createBoard(ctx, { onSelectAgent: (id) => selectAgent(id) });
    const deps = createDeps(ctx, { onSelectAgent: (id) => selectAgent(id) });
    const evBar = el("div", { class: "ag-evbar", hidden: true });
    const filterNote = el("div", { class: "ag-filternote", hidden: true });
    const tabs = ui.tabs({ id: "agents.bottom", label: "Timeline, task board and dependencies", active: "timeline", tabs: [
      { id: "timeline", label: "Event timeline", render: (p) => p.append(timeline.el) },
      { id: "board", label: "Task board", render: (p) => p.append(board.el) },
      { id: "deps", label: "Dependencies", render: (p) => p.append(deps.el) }] });
    const bottomCard = el("section", { class: "ag-card ag-tabs-card", "aria-label": "Timeline, task board and dependencies" }, evBar, filterNote, tabs.el);
    const bottom = el("div", { class: "ag-bottom" }, summary.el, bottomCard);
    const root = el("div", { class: "ag-root", dataset: { view: S.view } }, toolbar, banner, outcome, replayBar, main, bottom);
    host.replaceChildren(root);

    const rz = ui.resizer({ panel: inspectorAside, side: "left", id: "agents.inspector", min: 340, max: 620, width: 420, collapsible: true, label: "Resize inspector" });
    rz.collapse(true);
    inspectorAside.append(inspector.el);
    let insDrawer = null;

    // fill the visible workspace height (the shell scrolls #workspace; we never add our own page scroller)
    const wsEl = document.getElementById("workspace");
    function sizeRoot() {
      const cs = wsEl ? getComputedStyle(wsEl) : null;
      const h = wsEl ? wsEl.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom) : window.innerHeight - 80;
      root.style.setProperty("--ag-avail", Math.max(0, Math.round(h)) + "px");
    }
    sizeRoot();
    const rootRo = new ResizeObserver(() => { sizeRoot(); }); if (wsEl) rootRo.observe(wsEl); disposers.push(() => rootRo.disconnect());

    // ------------------------------------------------------------------ rendering (batched, never rebuilds the graph)
    function schedule() { if (S.rafId || dead) return; S.rafId = requestAnimationFrame(() => { S.rafId = 0; renderAll(); }); }
    const eventsById = () => new Map(shownEvents().map((e) => [e.event_id, e]));
    function hlState() {
      const e = S.selectedEvent; if (!e) return null;
      const ags = new Set(eventAgents(e, V.taskMap));
      const p = e.payload || {};
      return { agents: ags, pair: e.event_type === "message.sent" && p.sender && p.recipient ? pairKey(p.sender, p.recipient) : null, eventId: e.event_id,
        artifact: e.event_type === "artifact.created" && e.agent_id ? { agent: e.agent_id, path: p.path, title: p.title || null, eventId: e.event_id } : null };
    }
    function uiState() {
      const m = viewMode();
      return { selected: S.selected, filters: S.filters, refMs: refMs(), mode: m, hl: hlState(), canAct: canAct(), actReason: actReason(), events: shownEvents(), eventsById: eventsById(), selectedEvent: S.selectedEvent };
    }
    function renderAll() {
      if (dead) return;
      const evs = shownEvents();
      const key = `${mode()}|${S.runId}|${evs.length}|${evs.length ? evs[evs.length - 1].seq : 0}|${S.replayIdx}|${S.demo}`;
      if (key !== vKey) { V = derive(evs); vKey = key; }
      const u = uiState();
      graph.update(V, u); list.update(V, u); summary.update(V, u); timeline.update(V, u); board.update(V, u); deps.update(V, u);
      if (S.selected) inspector.update(V, u);
      renderChrome(u);
      const pend = S.pendingPulses; S.pendingPulses = [];
      for (const [a, b] of pend) graph.pulse(a, b);
    }

    function renderChrome(u) {
      const m = mode(), vm = u.mode;
      // run selector
      const opts = S.runs.map((r) => ({ id: r.run_id, label: `${r.run_id} \u00b7 ${plural(r.n, "event")}${r.has_demo ? " \u00b7 demo" : ""}` }));
      if (!opts.length) opts.push({ id: "", label: S.loaded ? "No runs recorded yet" : "Loading runs" });
      const sig = JSON.stringify(opts) + (S.runId || "");
      if (runSel.dataset.sig !== sig) { runSel.dataset.sig = sig; runSel.replaceChildren(...opts.map((o) => el("option", { value: o.id, selected: o.id === S.runId }, o.label))); runSel.value = S.runId || ""; }
      runSel.disabled = !!S.demo || !!S.replay;
      // badges
      const b = [];
      if (S.demo) b.push(ui.badge("demo", "DEMO (client-side)", "flask"));
      else if (V.onlyDemo) b.push(ui.badge("demo", "DEMO run (source: demo)", "flask"));
      if (S.replay) b.push(ui.badge("info", "REPLAY", "clock"));
      else if (!S.demo) {
        if (m === "live") {
          const c = S.conn;
          if (c.conn === "live") b.push(ui.badge("ok", "LIVE", "signal"));
          else if (c.conn === "stale") b.push(ui.badge("warn", "STALE", "alert"));
          else if (c.conn === "reconnecting") b.push(c.attempts >= 5 ? ui.badge("bad", "FAILED, retrying", "plug_off") : ui.badge("warn", `RECONNECTING (try ${c.attempts || 1})`, "plug_off"));
          else b.push(ui.badge("neutral", "CONNECTING", "clock"));
        } else b.push(ui.badge("neutral", "RECORDED RUN", "clock"));
      }
      const bsig = b.map((x) => x.textContent).join("|");
      if (modeSlot.dataset.sig !== bsig) { modeSlot.dataset.sig = bsig; modeSlot.replaceChildren(...b); }
      // view toggle
      viewSeg.replaceChildren(...[["graph", "Graph"], ["list", "List"]].map(([id, label]) => el("button", { type: "button", class: ["ag-seg-b", S.view === id ? "is-on" : ""], "aria-pressed": String(S.view === id), on: { click: () => setView(id) } }, label)));
      root.dataset.view = S.view; root.dataset.mode = vm;
      graphPane.hidden = S.view !== "graph"; listPane.hidden = S.view !== "list";
      // replay / demo buttons
      replayBtn.querySelector("span").textContent = S.replay ? "Exit replay" : "Replay";
      replayBtn.disabled = !S.replay && baseEvents().length === 0;
      demoBtn.querySelector("span").textContent = S.demo ? "Exit demo" : "Demo";
      startBtn.disabled = !canAct(); startBtn.title = canAct() ? "Record a task request (no runtime executes it)" : actReason();
      // filters
      const nf = Object.values(S.filters).filter(Boolean).length;
      filterBtn.querySelector("span").textContent = nf ? `Filters (${nf})` : "Filters";
      // banner
      let bn = null;
      if (S.demo) bn = ["demo", "flask", "DEMO DATA. Generated in this browser to show how the tower works. It is not real activity and is never stored or sent to the server."];
      else if (S.replay) bn = ["info", "clock", `REPLAY of ${S.runId}. Recorded history shown with original timestamps; nothing is posted. Live events are not shown until you exit replay.`];
      else if (V.onlyDemo) bn = ["demo", "flask", `Run ${S.runId} contains only source='demo' events. It is labelled DEMO and is not real activity.`];
      else if (m === "live" && S.conn.conn !== "live") bn = ["warn", "alert", S.conn.conn === "stale" ? `Event stream is stale. Towers show the last known state as of ${fmt.time(S.conn.lastBeat || Date.now(), { seconds: true })}; elapsed counters are paused.` : S.conn.conn === "reconnecting" ? "Event stream disconnected. Reconnecting automatically; towers show the last known state." : "Connecting to the event stream."];
      else if (m === "recorded") bn = ["info", "clock", `Viewing recorded run ${S.runId}, not the latest run. It does not update.`];
      else if (S.loadError) bn = ["bad", "x_circle", `Could not load events: ${S.loadError}`];
      else if (S.newRun && m === "live") bn = ["info", "info", `New activity in run ${S.newRun}. `];
      renderBanner(bn);
      renderReplayBar();
      renderEvBar();
      // filter note
      const fn = [];
      if (S.selected) fn.push(`agent ${ROLE[S.selected].name}`);
      if (S.filters.role) fn.push(`role ${ROLE[S.filters.role].name}`);
      if (S.filters.status) fn.push(`status ${STATUS_META[S.filters.status]?.label || S.filters.status}`);
      if (S.filters.type) fn.push(`event ${S.filters.type}`);
      filterNote.hidden = !fn.length;
      if (fn.length) filterNote.replaceChildren(ui.badge("info", `Filtered: ${fn.join(", ")}`, "list"), ui.btn("Clear filters", { onClick: () => { S.filters = { status: "", role: "", type: "" }; deselect(); schedule(); } }));
    }
    let bannerSig = "";
    function renderBanner(bn) {
      const sig = bn ? bn.join("|") + (S.newRun || "") : "";
      if (sig === bannerSig) return; bannerSig = sig;
      banner.hidden = !bn; if (!bn) { banner.replaceChildren(); return; }
      banner.className = `ag-banner is-${bn[0]}`;
      const sw = S.newRun && !S.replay && !S.demo && bn[0] === "info" && bn[1] === "info";
      banner.replaceChildren(...[ui.icon(bn[1], 16), el("span", {}, bn[2].trim()), sw ? ui.btn("Switch to it", { onClick: () => switchRun(S.newRun) }) : null].filter(Boolean));
    }
    function renderOutcome() {
      const o = S.outcome; outcome.hidden = !o; if (!o) return;
      outcome.className = `ag-outcome is-${o.kind}`;
      outcome.replaceChildren(ui.icon(o.kind === "ok" ? "check" : "alert", 16), el("span", {}, el("strong", {}, `${o.command.replace(/_/g, " ")}: `), o.message, o.note ? ` ${o.note}` : ""),
        el("button", { type: "button", class: "icon-btn", "aria-label": "Dismiss outcome", on: { click: () => { S.outcome = null; renderOutcome(); } } }, ui.icon("x", 14)));
    }
    function renderEvBar() {
      const e = S.selectedEvent; evBar.hidden = !e; if (!e) { evBar.replaceChildren(); return; }
      evBar.replaceChildren(el("span", { class: "ag-evbar-k" }, `Selected event #${e.seq}`), el("span", { class: "mono" }, e.event_type), el("span", { class: "muted" }, timeLabel(ctx, e.timestamp_utc, refMs())),
        el("span", { class: "ag-evbar-v" }, summarizeEvent(e, 400)), ui.btn("Clear", { onClick: () => selectEvent(null) }));
    }

    // ------------------------------------------------------------------ selection
    function openInspectorPanel() {
      if (isNarrow()) {
        if (insDrawer) return;
        insDrawer = ui.drawer({ title: `${ROLE[S.selected].name} (${S.selected})`, width: 480, label: "Agent inspector", content: (b) => b.append(inspector.el), onClose: () => { insDrawer = null; inspectorAside.append(inspector.el); if (S.selected) deselect(true); } });
      } else { rz.setWidth(rz.width()); }
    }
    function selectAgent(id, o = {}) {
      if (!AGENT_IDS.includes(id)) return;
      S.selected = id; inspector.show(id); openInspectorPanel();
      insDrawer?.setTitle(`${ROLE[id].name} (${id})`);
      renderAll();
      if (o.keyboard || isNarrow()) later(() => inspector.focusHeading(), 40);
      later(() => graph.relayout(), 60);
    }
    function deselect(fromDrawer) {
      const prev = S.selected; S.selected = null; inspector.clear();
      if (insDrawer && !fromDrawer) { const d = insDrawer; insDrawer = null; d.close(); inspectorAside.append(inspector.el); }
      rz.collapse(true);
      schedule(); later(() => graph.relayout(), 60);
      if (prev && !fromDrawer) later(() => { const t = graph.towerEl(prev); if (t && (root.contains(document.activeElement) || document.activeElement === document.body)) t.focus(); }, 80);
    }
    function selectEvent(e) {
      S.selectedEvent = !e || (S.selectedEvent && S.selectedEvent.seq === e.seq && S.selectedEvent.run_id === e.run_id) ? null : e;
      renderAll(); timeline.mark(S.selectedEvent?.seq ?? null);
    }
    function setView(v) { S.view = v; ctx.prefs.set("agents.view", v); renderAll(); if (v === "graph") later(() => graph.relayout(), 30); }

    // ------------------------------------------------------------------ edge details
    function edgeInfo(kind, grp, anchor) {
      if (!grp) return;
      ui.popover(anchor, (box) => {
        if (kind === "msg") {
          const m = grp.last;
          box.append(el("div", { class: "pop-title" }, `Message${grp.count > 1 ? "s" : ""}: ${grp.a} and ${grp.b}`),
            el("dl", { class: "kv" }, ...[["Sender", m.sender], ["Recipient", m.recipient], ["Time", timeLabel(ctx, m.ts, refMs())], ["Preview", m.preview], ["Kind", m.kind]].map(([k, v]) => el("div", { class: "kv-row" }, el("dt", {}, k), el("dd", {}, v)))),
            grp.count > 1 ? el("p", { class: "pop-note" }, `${grp.count} messages on this connector; showing the latest.`) : null,
            el("p", { class: "pop-note" }, "This solid line exists because a message.sent event was recorded."));
        } else {
          box.append(el("div", { class: "pop-title" }, `Task dependency: ${grp.a} and ${grp.b}`),
            el("ul", { class: "ag-ul" }, ...grp.items.map((d) => el("li", {}, `${d.task_id} (${ROLE[d.to_agent]?.short || "?"}) waits on ${d.depends_on} (${ROLE[d.from_agent]?.short || "?"})`))),
            el("p", { class: "pop-note" }, "Dashed line from task.dependency events. A dependency is not a message."));
        }
      }, { label: kind === "msg" ? "Message details" : "Dependency details", width: 340 });
    }

    // ------------------------------------------------------------------ filters + search
    function openFilters(anchor) {
      ui.popover(anchor, (box) => {
        const mk = (label, key, options) => {
          const s = el("select", { class: "input", "aria-label": label, on: { change: () => { S.filters[key] = s.value; schedule(); } } },
            el("option", { value: "" }, "Any"), ...options.map(([v, t]) => el("option", { value: v, selected: S.filters[key] === v }, t)));
          return el("label", { class: "field" }, el("span", {}, label), s);
        };
        box.append(el("div", { class: "pop-title" }, "Filters"),
          mk("Status", "status", ["not_started", "no_status", "queued", "working", "awaiting_dependency", "awaiting_approval", "idle", "failed", "complete"].map((s) => [s, STATUS_META[s].label])),
          mk("Role", "role", AGENT_IDS.map((a) => [a, `${ROLE[a].name} (${a})`])),
          mk("Event type", "type", EVENT_TYPES.map((t) => [t, t])),
          el("p", { class: "pop-note" }, "Status and role dim non-matching towers and filter the list, timeline, board and dependencies. Event type filters the timeline."),
          ui.btn("Clear filters", { onClick: () => { S.filters = { status: "", role: "", type: "" }; schedule(); ui.closePopover(); } }));
      }, { label: "Filters", width: 320, placement: "bottom-end" });
    }

    let searchTimer = 0;
    function closeSearch() { searchPop.hidden = true; searchPop.replaceChildren(); }
    async function runSearch() {
      const q = searchIn.value.trim(); const my = ++S.searchSeq;
      if (!q) { closeSearch(); return; }
      const ql = q.toLowerCase();
      const agentHits = AGENT_IDS.filter((a) => a.includes(ql) || ROLE[a].name.toLowerCase().includes(ql));
      const taskHits = V.tasks.filter((t) => t.task_id.toLowerCase().includes(ql) || t.title.toLowerCase().includes(ql)).slice(0, 6);
      let evHits = [], err = null;
      if (S.demo) evHits = S.demoEvents.filter((e) => JSON.stringify(e.payload).toLowerCase().includes(ql) || e.event_type.includes(ql) || (e.agent_id || "").includes(ql)).slice(-20).reverse();
      else { try { const r = await ctx.api.get(`/api/search?q=${encodeURIComponent(q)}&limit=30${S.runId ? "&run_id=" + encodeURIComponent(S.runId) : ""}`); evHits = r.events || []; } catch (e) { err = e.message; } }
      if (my !== S.searchSeq || dead) return;
      const item = (kids, fn) => el("button", { type: "button", role: "option", class: "ag-sr-item", on: { click: () => { fn(); closeSearch(); } } }, kids);
      const sect = (t, items, empty) => el("div", { class: "ag-sr-sect" }, el("div", { class: "ag-sr-h" }, t), ...(items.length ? items : [el("div", { class: "ag-sr-none muted" }, empty)]));
      searchPop.replaceChildren(
        sect(`Agents (${agentHits.length})`, agentHits.map((a) => item([el("strong", {}, ROLE[a].name), " ", el("span", { class: "mono muted" }, a)], () => selectAgent(a))), "No matching agent"),
        sect(`Tasks (${taskHits.length})`, taskHits.map((t) => item([el("span", { class: "mono" }, t.task_id), " ", t.title], () => t.owner && selectAgent(t.owner))), "No matching task in this run"),
        sect(`Events (${evHits.length}${S.demo ? ", demo" : ""})`, evHits.slice(0, 12).map((e) => item([el("span", { class: "mono ag-evtype" }, e.event_type), " ", el("span", { class: "muted" }, `#${e.seq} ${e.run_id}`), el("div", {}, summarizeEvent(e, 140))], () => openSearchEvent(e))),
          err ? `Search failed: ${err}` : "No matching events"));
      searchPop.hidden = false;
    }
    async function openSearchEvent(e) {
      if (!S.demo && e.run_id !== S.runId) await switchRun(e.run_id);
      const found = baseEvents().find((x) => x.seq === e.seq) || e;
      S.selectedEvent = null; selectEvent(found); tabs.select("timeline");
      later(() => timeline.el.querySelector("tr.is-sel")?.scrollIntoView({ block: "nearest" }), 60);
    }
    searchIn.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 280); });
    searchIn.addEventListener("keydown", (e) => { if (e.key === "Escape" && !searchPop.hidden) { e.stopPropagation(); closeSearch(); } if (e.key === "Enter") { clearTimeout(searchTimer); runSearch(); } });
    const outside = (e) => { if (!searchPop.hidden && !e.target.closest(".ag-searchwrap")) closeSearch(); };
    document.addEventListener("pointerdown", outside, true); disposers.push(() => document.removeEventListener("pointerdown", outside, true));

    // ------------------------------------------------------------------ data loading
    async function loadRuns() {
      try { const r = await ctx.api.get("/api/runs"); S.runs = r.runs || []; S.latestRun = r.latest || (S.runs[0]?.run_id ?? null); }
      catch (e) { S.loadError = e.message; }
    }
    async function loadEvents(runId) {
      const all = []; let after = 0;
      for (let guard = 0; guard < 200; guard++) {
        const r = await ctx.api.get(`/api/events?run_id=${encodeURIComponent(runId)}&after_seq=${after}&limit=5000`);
        const chunk = r.events || []; all.push(...chunk);
        if (chunk.length < 5000) break; after = chunk[chunk.length - 1].seq;
      }
      return all;
    }
    async function loadRun(runId) {
      S.loaded = false; S.runId = runId; S.events = []; S.seqs = new Set(); S.eventIds = new Set(); S.loadError = null; S.newRun = null; graph.resetEdges();
      vKey = "";
      if (runId) {
        try {
          const evs = await loadEvents(runId);
          if (dead || S.runId !== runId) return;
          for (const e of evs) { if (!S.seqs.has(e.seq)) { S.events.push(e); S.seqs.add(e.seq); S.eventIds.add(e.event_id); } }
          S.events.sort((a, b) => a.seq - b.seq);
          S.loadedMax = S.events.length ? S.events[S.events.length - 1].seq : 0;
        } catch (e) { S.loadError = e.message; }
      }
      S.loaded = true;
      const buf = S.buffer; S.buffer = [];
      for (const [e, meta] of buf) ingest(e, meta);
      renderAll(); later(() => graph.relayout(), 30);
    }
    async function switchRun(runId) {
      if (!runId || runId === S.runId) return;
      if (S.replay) exitReplay(true);
      S.selectedEvent = null; S.outcome = null; renderOutcome();
      await loadRun(runId);
    }
    function pickInitialRun() {
      const real = S.runs.find((r) => !r.has_demo);
      return (real || S.runs[0])?.run_id || null;
    }

    // live events (ctx.events: SSE with Last-Event-ID resume, deduped by seq in core AND here by seq/event_id)
    function ingest(e, meta) {
      if (!e || typeof e.seq !== "number") return;
      if (e.run_id !== S.runId) {
        if (!S.runs.some((r) => r.run_id === e.run_id)) { loadRuns().then(() => { if (!dead) { if (e.run_id === S.latestRun && S.runId !== S.latestRun) S.newRun = e.run_id; schedule(); } }); }
        else if (e.run_id === S.latestRun && S.runId !== e.run_id) { S.newRun = e.run_id; schedule(); }
        return;
      }
      if (S.seqs.has(e.seq) || S.eventIds.has(e.event_id)) return;
      S.seqs.add(e.seq); S.eventIds.add(e.event_id);
      const last = S.events[S.events.length - 1];
      if (!last || e.seq > last.seq) S.events.push(e); else { S.events.push(e); S.events.sort((a, b) => a.seq - b.seq); }
      const r = S.runs.find((x) => x.run_id === e.run_id); if (r) { r.n += 1; r.last_seq = Math.max(r.last_seq, e.seq); }
      if (!meta?.replay && e.seq > S.loadedMax && e.event_type === "message.sent" && mode() === "live" && !S.replay && !S.demo) {
        const p = e.payload || {}; if (p.sender && p.recipient) S.pendingPulses.push([p.sender, p.recipient]);   // REAL message.sent only
      }
      if (S.replay || S.demo) return;     // buffered into the real store; not shown until replay/demo ends
      schedule();
    }
    disposers.push(ctx.events.on("*", (e, meta) => { if (!S.loaded) S.buffer.push([e, meta]); else ingest(e, meta); }));
    disposers.push(ctx.events.onState((st) => { S.conn = st; schedule(); }));

    // ------------------------------------------------------------------ replay
    function startReplay() {
      const base = baseEvents().slice(); if (!base.length) return;
      S.outcome = null; renderOutcome();
      S.replayBase = base; S.replayIdx = 0;
      S.replay = createPlayer({ events: base, onChange: (st, revealed) => {
        S.replayIdx = st.idx; S.replayState = st;
        for (const e of revealed) if (e.event_type === "message.sent" && e.payload?.sender && e.payload?.recipient) S.pendingPulses.push([e.payload.sender, e.payload.recipient]);  // replayed message => pulse
        schedule();
      } });
      S.replayState = S.replay.state(); S.selectedEvent = null; graph.resetEdges(); vKey = "";
      renderAll(); S.replay.play();
    }
    function exitReplay(silent) {
      S.replay?.dispose(); S.replay = null; S.replayBase = []; S.replayIdx = 0; S.replayState = null; S.pendingPulses = []; S.selectedEvent = null; graph.resetEdges(); vKey = "";
      if (!silent) renderAll();
    }
    function renderReplayBar() {
      const r = S.replay; replayBar.hidden = !r; if (!r) { replayBar.replaceChildren(); replayBar.dataset.built = ""; return; }
      const st = S.replayState || r.state();
      if (!replayBar.dataset.built) {
        replayBar.dataset.built = "1";
        const play = ui.btn("Pause", { icon: "pause", onClick: () => r.toggle(), title: "Play or pause" }); play.id = "ag-rp-play";
        const speed = el("select", { class: "input ag-speed", "aria-label": "Replay speed", id: "ag-rp-speed", on: { change: () => r.setSpeed(Number(speed.value)) } }, ...SPEEDS.map((x) => el("option", { value: x, selected: x === 1 }, `${x}x`)));
        const scrub = el("input", { type: "range", min: 0, max: st.n, value: 0, class: "ag-scrub", id: "ag-rp-scrub", "aria-label": "Replay position (events revealed)", on: { input: () => { r.pause(); r.seek(Number(scrub.value)); } } });
        replayBar.append(el("span", { class: "ag-rp-badge" }, ui.badge("info", "REPLAY MODE", "clock")), play, el("label", { class: "ag-rp-sp" }, el("span", { class: "sr-only" }, "Speed"), speed), scrub,
          el("div", { class: "ag-rp-info" }, el("div", { class: "ag-rp-time", id: "ag-rp-time" }), el("div", { class: "ag-rp-sub muted", id: "ag-rp-sub" })), el("span", { class: "ag-rp-gap", id: "ag-rp-gap", hidden: true }));
      }
      const q = (id) => replayBar.querySelector("#" + id);
      const play = q("ag-rp-play"); play.querySelector("span").textContent = st.playing ? "Pause" : (st.idx >= st.n ? "Replay again" : "Play");
      q("ag-rp-scrub").value = String(st.idx); q("ag-rp-scrub").max = String(st.n);
      const lastRev = S.replayBase[st.idx - 1], next = S.replayBase[st.idx];
      q("ag-rp-time").textContent = lastRev ? `Original time ${fmt.time(tms(lastRev.timestamp_utc), { seconds: true })}` : `Not started. First event ${fmt.time(tms(S.replayBase[0].timestamp_utc), { seconds: true })}`;
      q("ag-rp-sub").textContent = `${st.idx} of ${st.n} events revealed${next ? "" : " (end of run)"}. Recorded run ${S.runId}${S.demo ? " (DEMO)" : ""}.`;
      const gap = q("ag-rp-gap"); gap.hidden = !(st.gapMs > 0); gap.textContent = st.gapMs > 0 ? gapLabel(st.gapMs) : "";
      gap.title = "Quiet periods longer than 5 s are compressed to 1.2 s of playback. The original timestamps are unchanged.";
    }

    // ------------------------------------------------------------------ demo (separate client-side state)
    function toggleDemo() {
      S.outcome = null; renderOutcome();
      if (S.replay) exitReplay(true);
      S.demo = !S.demo; S.selectedEvent = null; graph.resetEdges(); vKey = "";
      S.demoEvents = S.demo ? demoEvents() : [];
      if (S.demo) S.selected && deselect();
      renderAll(); later(() => graph.relayout(), 30);
    }

    // ------------------------------------------------------------------ keyboard
    const onKey = (e) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (!searchPop.hidden) return;
      if (S.selected && !insDrawer) { e.preventDefault(); deselect(); return; }
      if (S.selectedEvent) { e.preventDefault(); selectEvent(null); }
    };
    root.addEventListener("keydown", onKey);
    const mq = window.matchMedia(`(max-width: ${NARROW_PX - 1}px)`);
    const onMq = () => {
      if (!ctx.prefs.get("agents.view", null)) { S.view = mq.matches ? "list" : "graph"; }
      if (S.selected) { const id = S.selected; if (insDrawer) { const d = insDrawer; insDrawer = null; d.close(); inspectorAside.append(inspector.el); } S.selected = null; selectAgent(id); }
      renderAll(); later(() => graph.relayout(), 40);
    };
    mq.addEventListener("change", onMq); disposers.push(() => mq.removeEventListener("change", onMq));

    // ------------------------------------------------------------------ boot
    cleanup = () => {
      dead = true; cancelAnimationFrame(S.rafId); S.replay?.dispose();
      timers.forEach(clearTimeout); clearTimeout(searchTimer);
      disposers.forEach((f) => { try { f(); } catch { /* ignore */ } });
      graph.destroy(); try { insDrawer?.close(); } catch { /* ignore */ }
      ui.closePopover?.();
      document.querySelector("link[data-agents-css]")?.remove();
    };
    renderAll();
    try { const snap = await ctx.events.snapshot(); if (snap?.concurrency?.budget_max) S.budgetMax = snap.concurrency.budget_max; } catch { /* optional */ }
    await loadRuns();
    if (dead) return;
    await loadRun(pickInitialRun());
    if (dead) return;
    // deep link: #/agents?agent=backtester
    const p0 = ctx.route?.params || {};
    if (p0.agent && AGENT_IDS.includes(p0.agent)) selectAgent(p0.agent);

    // test hook (read-only view of state; no write access to the real store)
    window.__agents = { state: () => ({ mode: mode(), runId: S.runId, count: baseEvents().length, shown: shownEvents().length, seqs: [...S.seqs], selected: S.selected, replay: S.replayState, demo: S.demo, conn: S.conn.conn }),
      graph, view: () => V };
  },
  unmount() { try { cleanup?.(); } finally { cleanup = null; try { delete window.__agents; } catch { /* ignore */ } } },
};
