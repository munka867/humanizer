// Command Center (MARKET worker): account summary (honest when no broker), watchlist | chart | activity+risk, positions/orders/fills/signals, agent summary.
// Read-only. No order submission path exists in this module.
import { el } from "../core/dom.js";
import { fmt } from "../core/format.js";
import { ensureCss } from "../market/util.js";
import { createChartPanel } from "../market/chartpanel.js";
import { createWatchState, createCompactWatchlist } from "../market/watchlist.js";
import { loadInstruments, loadPortfolio, loadSignals, pickSelected, saveSelected } from "../market/data.js";
import { metricCards, makeTable } from "../market/tables.js";

let dispose = [];

export default {
  id: "command_center", title: "Command Center", icon: "command_center",
  async mount(host, ctx, params = {}) {
    await ensureCss();
    const ui = ctx.ui;
    host.classList.add("mk-cc");
    const S = createWatchState(ctx);
    let selected = null, pf = null, chartP = null;

    // ---- status strip + metrics
    const strip = el("div", { class: "mk-strip", "data-testid": "broker-strip" });
    const metrics = el("div", { class: "mk-metrics", role: "group", "aria-label": "Account summary" });
    const top = el("div", { class: "mk-top" }, strip, metrics);
    const drawTop = () => {
      const broker = pf?.broker;
      strip.replaceChildren(
        ui.badge("warn", broker ? "Research mode: no broker connected" : "Portfolio data unavailable", "plug_off"),
        el("span", { class: "muted mk-strip-text", title: "The connected IBKR account was read once on 2026-10-08 and was empty/new. This app has no live account feed, so no account figure is shown." }, pf ? "Account figures are unavailable, not zero. No live account feed." : "Could not load /api/portfolio."),
        el("span", { class: "spacer" }), showWl, showSide,
        ui.btn("Connect broker", { kind: "default", onClick: () => ctx.nav("data") }));
      metrics.replaceChildren(...metricCards(ctx, pf));
    };

    // ---- main: watchlist | chart | activity
    const wlPanelHost = el("div", { class: "mk-side mk-side-left" });
    let rzL, rzR;
    const showWl = ui.btn("Watchlist", { icon: "list", kind: "ghost", title: "Show or hide the watchlist panel", onClick: () => rzL?.toggle() });
    const showSide = ui.btn("Activity", { icon: "gauge", kind: "ghost", title: "Show or hide the activity and risk panel", onClick: () => rzR?.toggle() });
    chartP = createChartPanel(ctx, { compact: true });
    const center = el("div", { class: "mk-center" }, chartP.el);
    const sidePanel = el("aside", { class: "mk-side mk-side-right", "aria-label": "Activity and risk" });
    const main = el("div", { class: "mk-main" }, wlPanelHost, center, sidePanel);
    rzL = ui.resizer({ panel: wlPanelHost, side: "right", id: "cc.watch", min: 240, max: 420, width: 270, label: "Resize watchlist panel" });
    rzR = ui.resizer({ panel: sidePanel, side: "left", id: "cc.side", min: 260, max: 460, width: 310, label: "Resize activity panel" });

    // narrow window and no saved layout yet: give the chart the room (the user can reopen the panel with the strip buttons)
    if (host.clientWidth && host.clientWidth < 1150 && host.clientWidth > 1000 && ctx.prefs.get("panel.cc.side", null) == null) rzR.collapse(true);
    drawTop();
    const wl = createCompactWatchlist(ctx, S, { onSelect: select, getSelected: () => selected, onManage: () => ctx.nav("stocks", selected ? { conid: selected.conid, ex: selected.exchange } : {}) });
    wlPanelHost.append(wl.el);
    function select(ins) { if (!ins) return; selected = ins; saveSelected(ctx, ins); chartP.setInstrument(ins).catch(() => {}); wl.el.querySelectorAll(".mk-wl-row").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.key === ins.key))); }

    // ---- right panel: events, risk, freshness
    const evBox = el("ul", { class: "mk-events", "aria-label": "Recent events" }), riskBox = el("div", { class: "mk-risk" }), freshBox = el("ul", { class: "mk-fresh", "aria-label": "Data freshness" });
    const sec = (t, ...k) => el("section", { class: "mk-sec" }, el("h3", { class: "mk-h3" }, t), ...k);
    sidePanel.append(el("div", { class: "mk-side-in" }, sec("Recent events", evBox), sec("Risk", riskBox), sec("Data freshness", freshBox)));
    const evSummary = (e) => { const p = e.payload || {}; if (e.event_type === "test.result" && p.name) return `${p.name}: ${p.outcome || "?"}${p.detail ? " (" + p.detail + ")" : ""}`; return String(p.summary || p.preview || p.title || p.task || p.message || p.status || p.mode || "").slice(0, 120); };
    const drawEvents = () => {
      const recent = ctx.events.recent(3).slice().reverse();
      evBox.replaceChildren(...(recent.length ? recent.map((e) => el("li", { class: "mk-ev" }, el("span", { class: "mk-ev-t muted num" }, fmt.time(e.timestamp_utc, { date: false, seconds: false })), el("span", { class: "mk-ev-type" }, e.event_type), el("span", { class: "mk-ev-sum" }, `${e.agent_id ? e.agent_id + ": " : ""}${evSummary(e)}`))) : [el("li", { class: "muted" }, "No events received yet. Events appear here as agents and tools report in.")]));
    };
    const drawRisk = () => {
      const r = ctx.risk.get();
      riskBox.replaceChildren(r.policy ? ui.badge("ok", `Policy: ${r.policy.name || "set"}`, "shield") : ui.badge("warn", "No risk policy configured", "shield_off"),
        el("dl", { class: "kv mk-kv-tight" }, el("div", { class: "kv-row" }, el("dt", {}, "Limit usage"), el("dd", {}, r.usage?.daily_loss == null && r.usage?.position_value == null ? fmt.missing(r.policy ? "Not reported yet" : "Unavailable: no approved risk policy") : `daily loss ${r.usage.daily_loss ?? "—"}; position ${r.usage.position_value ?? "—"}`))),
        el("p", { class: "muted mk-fine" }, `As of ${r.asOf ? fmt.time(r.asOf) : "—"}. Limits will be enforced in code by a future risk engine.`));
    };
    const FEEDS = [["quotes", "Market data"], ["broker", "Broker"], ["agents", "Agent events"]];
    const drawFresh = () => freshBox.replaceChildren(...FEEDS.map(([f, label]) => { const i = ctx.freshness.get(f); return el("li", { class: "mk-fr" }, el("span", { class: "mk-fr-l" }, label), ui.freshBadge(f, { showAsOf: false }), el("span", { class: "muted mk-fr-d", title: i.detail || "" }, [i.asOf ? `as of ${/^\d{4}-\d{2}-\d{2}$/.test(i.asOf) ? i.asOf : fmt.time(i.asOf, { seconds: false })}` : null, i.source || i.detail].filter(Boolean).join(" · ") || "—")); }));
    drawEvents(); drawRisk(); drawFresh();
    let evT = null;
    dispose.push(ctx.events.on("*", () => { clearTimeout(evT); evT = setTimeout(() => { drawEvents(); scheduleAgents(); }, 250); }));
    dispose.push(ctx.risk.on(drawRisk), ctx.freshness.on(drawFresh));

    // ---- lower: tabs + agent summary
    const tabPanels = {}, sigRef = { v: undefined };
    const paint = (k) => {
      const p = tabPanels[k]; if (!p) return;
      const why = pf?.unavailable_reason || (pf ? "unavailable: no broker adapter implemented" : "Portfolio data failed to load");
      const action = { label: "Connect broker", onClick: () => ctx.nav("data") };
      if (k === "signals") return sigRef.v === undefined ? p.replaceChildren(ui.skeleton({ lines: 2 })) : drawSignals(sigRef.v);
      const [kind, rows] = { positions: ["positions", pf?.positions], orders: ["orders", pf?.working_orders], fills: ["fills", pf?.fills] }[k];
      p.replaceChildren(makeTable(ctx, kind, rows, why, { action, maxHeight: 112, exportable: false, columnMenu: false }).el);
    };
    const lowerTabs = ui.tabs({ id: "cc.lower", label: "Portfolio and signals", tabs: ["positions:Positions", "orders:Working orders", "fills:Fills", "signals:Strategy signals"].map((x) => { const [id, label] = x.split(":"); return { id, label, render: (p) => { tabPanels[id] = p; paint(id); } }; }) });
    const drawTabs = () => Object.keys(tabPanels).forEach(paint);
    const drawSignals = (sg) => { sigRef.v = sg; tabPanels.signals?.replaceChildren(ui.table({ id: "cc.signals", ariaLabel: "Strategy signals", caption: "Strategy signals", rows: sg?.signals || [], rowKey: (r, i) => r.id || i, compact: true, maxHeight: 112, exportable: false,
      columns: [{ key: "time", label: "Time", width: 160 }, { key: "symbol", label: "Symbol", width: 90 }, { key: "strategy", label: "Strategy", width: 140 }, { key: "action", label: "Signal", width: 120 }],
      emptyTitle: "No strategy signals", emptyWhy: sg ? `${sg.reason}. Signals appear only from an approved strategy; none exists, and no signal is ever invented.` : "Signals failed to load." }).el); };
    const agentBox = el("section", { class: "mk-panel mk-agents", "aria-label": "Agent team summary" });
    const lower = el("div", { class: "mk-lower" }, el("div", { class: "mk-lower-tabs" }, lowerTabs.el), agentBox);
    let agT = null, snapErr = null, snap = null;
    const drawAgents = () => {
      const ag = snap?.agents ? Object.values(snap.agents) : null, tk = snap?.tasks ? Object.values(snap.tasks) : [];
      const cnt = (f) => (ag ? ag.filter(f).length : null);
      const active = cnt((a) => a.status === "working"), idle = cnt((a) => !a.status || a.status === "idle" || a.status === "complete");
      const queued = ag ? ag.filter((a) => a.status === "queued").length + tk.filter((t) => t.status === "queued").length : null;
      const stat = (k, v, why) => el("div", { class: "mk-stat", dataset: { stat: k } }, el("b", { class: "num" }, v == null ? fmt.missing(why) : String(v)), el("span", { class: "muted" }, k));
      agentBox.replaceChildren(el("div", { class: "mk-panel-head" }, el("h3", {}, "Agent team"), el("span", { class: "spacer" }), el("a", { class: "btn btn-ghost", href: "#/agents" }, "Open Agent Team")),
        el("div", { class: "mk-stats" }, stat("active", active, snapErr || "No snapshot"), stat("queued", queued, snapErr || "No snapshot"), stat("idle", idle, snapErr || "No snapshot")),
        el("p", { class: "muted mk-fine" }, snapErr ? `Agent status unavailable: ${snapErr}` : snap?.has_demo ? "Includes DEMO events (labelled in Agent Team)." : `From the event stream, snapshot seq ${snap?.seq ?? "—"}.`));
    };
    const loadAgents = async () => { try { snap = await ctx.events.snapshot(); snapErr = null; } catch (e) { snapErr = e.message; } drawAgents(); };
    const scheduleAgents = () => { clearTimeout(agT); agT = setTimeout(loadAgents, 1500); };
    drawAgents(); loadAgents();
    dispose.push(() => { clearTimeout(evT); clearTimeout(agT); });

    host.replaceChildren(top, main, lower);

    // ---- data
    S.load().then(async () => {
      try { const u = await loadInstruments(ctx); selected = pickSelected(ctx, u.instruments, params, S.rows()[0]?.key); } catch { selected = null; }
      if (!selected) { chartP.el.querySelector(".mk-cwrap").replaceChildren(ui.empty("No instrument selected", "No instruments with daily data are available. Check Data & Connections.", { label: "Open Data & Connections", onClick: () => ctx.nav("data") })); return; }
      select(selected);
    });
    loadPortfolio(ctx).then((r) => { pf = r; }).catch(() => { pf = null; }).then(() => { drawTop(); drawTabs(); });
    loadSignals(ctx).then(drawSignals).catch(() => drawSignals(null));
    host.__cc = { S };
  },
  unmount() { dispose.forEach((f) => { try { f(); } catch { /* ignore */ } }); dispose = []; },
};
