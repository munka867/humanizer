// PLACEHOLDER workspaces (SHELL worker). Each workspaces/<id>.js currently re-exports makePlaceholder('<id>').
// Replacing workspaces/<id>.js with a real module replaces the screen; nothing else needs to change.
// Placeholders show an honest capability state plus live data that already exists, never fabricated numbers.
import { el } from "../core/dom.js";
import { icon } from "../core/icons.js";
import { WORKSPACES, byId } from "../core/registry.js";

const BLURB = {
  command_center: "Overview of instruments, data freshness, agent activity and risk in one place.",
  stocks: "Daily-bar charts and instrument detail keyed by IBKR contract id and exchange.",
  portfolio: "Positions, orders and account state. Needs a broker connection that does not exist yet.",
  agents: "Agent tower view and inspector built from the event stream.",
  research: "Research library, strategy registry and experiment log.",
  backtest: "Backtest results and Monte Carlo resampling of the same evidence.",
  data: "Data sources, connection health and imports.",
  settings: "Preferences and the configuration audit trail.",
};
const ACTIONS = {
  command_center: [["Open Data & Connections", "data"], ["Browse instruments", "stocks"]],
  stocks: [["Open Data & Connections", "data"]],
  portfolio: [["Set up broker connection", "data"]],
  agents: [["Open Settings & Audit", "settings"]],
  research: [["Open Backtesting", "backtest"]],
  backtest: [["Open Research", "research"]],
  data: [["Open Settings & Audit", "settings"]],
  settings: [["Open Data & Connections", "data"]],
};

function card(title, ...kids) { return el("section", { class: "ph-card" }, el("h3", {}, title), ...kids); }

const EXTRAS = {
  async instruments(ctx, host, params) {
    const slot = el("div", {}, ctx.ui.skeleton({ lines: 3 })); host.append(card("Instruments with local daily bars", slot));
    try {
      const r = await ctx.api.get("/api/instruments?limit=200");
      const t = ctx.ui.table({ id: "ph.instruments", ariaLabel: "Instruments", caption: "Instruments with local daily bars", exportName: "instruments", rows: r.instruments, rowKey: (x) => x.key,
        emptyTitle: "No instruments", emptyWhy: r.reason || "No IBKR connector daily-bar files found.",
        columns: [{ key: "symbol", label: "Symbol", width: 90 }, { key: "name", label: "Name", width: 200, missing: "Company name is not in the data manifest" },
          { key: "exchange", label: "Exchange", width: 100 }, { key: "con_id", label: "IBKR conId", align: "num", width: 110, format: "qty", formatOpts: { dp: 0 }, missing: "No contract id in manifest" },
          { key: "currency", label: "Ccy", width: 70, value: (x) => x.currency, missing: "Not stated in manifest" },
          { key: "last", label: "Last bar", width: 120, value: (x) => x.data.last && String(x.data.last).slice(0, 10), missing: "No bars" },
          { key: "n", label: "Bars", align: "num", width: 80, value: (x) => x.data.n_bars, format: "qty" },
          { key: "ret", label: "Retrieved", width: 110, value: (x) => x.data.retrieved_on, missing: "Unknown" }] });
      slot.replaceChildren(t.el);
      if (params?.symbol) slot.prepend(el("p", { class: "muted" }, `Selected from search: ${params.symbol} (conId ${params.conid ?? "unknown"}, ${params.ex ?? "unknown"} exchange). Chart view is provided by the Stocks & Charts module.`));
    } catch (e) { ctx.ui.inlineError(slot, `Could not load instruments: ${e.message}`); }
  },
  async broker(ctx, host) {
    const b = ctx.env.broker;
    host.append(card("Broker", ctx.ui.badge("warn", "Broker not connected", "plug_off"), el("p", { class: "muted" }, `${b.reason}. Trading setup incomplete: there is no broker adapter and no risk engine. Account is new and empty, so no balances are shown rather than showing zeros.`)));
  },
  async agents(ctx, host) {
    const slot = el("div", {}, ctx.ui.skeleton({ lines: 3 })); host.append(card("Recent events (live)", slot));
    const rows = () => ctx.events.recent(30).reverse();
    const t = ctx.ui.table({ id: "ph.events", ariaLabel: "Recent events", caption: "Recent events", exportName: "events", rows: rows(), rowKey: (e) => e.seq, pageSize: 100, emptyTitle: "No events yet", emptyWhy: "Post events with command_center/emit.py or POST /api/events.",
      columns: [{ key: "seq", label: "Seq", align: "num", width: 70 }, { key: "t", label: "Time", width: 190, value: (e) => e.timestamp_utc, render: (e) => ctx.fmt.timeEl(e.timestamp_utc) },
        { key: "event_type", label: "Type", width: 150 }, { key: "agent_id", label: "Agent", width: 150, missing: "Not tied to one agent" }, { key: "source", label: "Source", width: 80 },
        { key: "s", label: "Summary", width: 380, value: (e) => e.payload.summary || e.payload.preview || e.payload.title || e.payload.status || e.payload.tool || "" }],
      expand: (e, td) => td.append(el("pre", { class: "ws-error-msg" }, JSON.stringify(e.payload, null, 2))) });
    slot.replaceChildren(t.el);
    const off = ctx.events.on("*", () => t.setRows(rows())); host._off.push(off);
  },
  async docs(ctx, host) { return listing(ctx, host, "/api/docs", "Research documents (read-only index)", "No documents found in research/docs."); },
  async results(ctx, host) { return listing(ctx, host, "/api/results", "Result files (read-only index)", "No result files in research/results/daily yet. Nothing has been run through the UI."); },
  async connections(ctx, host) {
    const slot = el("div", {}, ctx.ui.skeleton({ lines: 4 })); host.append(card("Connections (live from /api/connections)", slot));
    try {
      const c = await ctx.api.get("/api/connections");
      const flat = [["Broker", c.broker.reason], ["Execution", c.broker.execution], ["Market data source", c.market_data.source], ["Market data state", c.market_data.state],
        ["Interval", c.market_data.interval], ["Instruments", c.market_data.instruments], ["Last bar", c.market_data.last_bar], ["Retrieved on", c.market_data.last_retrieved_on],
        ["Source delay (s)", Array.isArray(c.market_data.delayed_seconds) ? c.market_data.delayed_seconds.join(", ") : c.market_data.delayed_seconds], ["Event stream max seq", c.event_stream.max_seq], ["Events DB", c.storage.events_db]];
      slot.replaceChildren(ctx.ui.table({ id: "ph.conn", ariaLabel: "Connections", rows: flat.map(([k, v]) => ({ k, v })), rowKey: (r) => r.k, exportable: false, columnMenu: false,
        columns: [{ key: "k", label: "Item", width: 200 }, { key: "v", label: "Value", width: 460, missing: "Not reported" }] }).el);
    } catch (e) { ctx.ui.inlineError(slot, `Could not load connections: ${e.message}`); }
  },
  async audit(ctx, host) {
    const slot = el("div", {}, ctx.ui.skeleton({ lines: 3 })); host.append(card("Configuration audit log", slot));
    try {
      const r = await ctx.api.get("/api/audit?limit=200");
      slot.replaceChildren(ctx.ui.table({ id: "ph.audit", ariaLabel: "Audit log", caption: "Audit log", exportName: "audit", rows: r.items, rowKey: (x) => x.seq, emptyTitle: "No audit entries", emptyWhy: "Changes such as display-mode changes (with the API token set) are recorded here.",
        columns: [{ key: "seq", label: "Seq", align: "num", width: 70 }, { key: "t", label: "Time", width: 190, value: (x) => x.timestamp_utc, render: (x) => ctx.fmt.timeEl(x.timestamp_utc) },
          { key: "area", label: "Area", width: 80 }, { key: "action", label: "Action", width: 140 }, { key: "outcome", label: "Outcome", width: 90, missing: "Not stated" },
          { key: "old", label: "From", width: 110, value: (x) => x.old == null ? null : String(x.old), missing: "—" }, { key: "new", label: "To", width: 110, value: (x) => x.new == null ? null : String(x.new), missing: "—" }, { key: "reason", label: "Reason", width: 320, missing: "None given" }] }).el);
    } catch (e) { ctx.ui.inlineError(slot, `Could not load audit log: ${e.message}`); }
  },
};
async function listing(ctx, host, url, title, whyEmpty) {
  const slot = el("div", {}, ctx.ui.skeleton({ lines: 3 })); host.append(card(title, slot));
  try {
    const r = await ctx.api.get(url);
    slot.replaceChildren(ctx.ui.table({ id: "ph." + url.split("/").pop(), ariaLabel: title, caption: title, exportName: url.split("/").pop(), rows: r.items, rowKey: (x) => x.name, emptyTitle: "Nothing here yet", emptyWhy: whyEmpty,
      columns: [{ key: "title", label: "Title", width: 360 }, { key: "name", label: "File", width: 220 }, { key: "bytes", label: "Bytes", align: "num", width: 90, format: "qty" }, { key: "modified", label: "Modified", width: 200, render: (x) => ctx.fmt.timeEl(x.modified) }] }).el);
  } catch (e) { ctx.ui.inlineError(slot, `Could not load: ${e.message}`); }
}
const EXTRA_FOR = { command_center: ["instruments"], stocks: ["instruments"], portfolio: ["broker"], agents: ["agents"], research: ["docs"], backtest: ["results"], data: ["connections"], settings: ["audit"] };

export function makePlaceholder(id) {
  const w = byId(id);
  let off = [];
  return {
    id, title: w.title, icon: w.icon, placeholder: true,
    async mount(host, ctx, params) {
      off = [];
      const root = el("div", { class: "ph" }); root._off = off;
      const head = el("div", { class: "ph-head" });
      const paint = () => {
        const c = ctx.env.capability(id);
        head.replaceChildren(el("h2", {}, w.title), ctx.ui.badge(c.tone === "ok" ? "ok" : c.tone === "warn" ? "warn" : "neutral", c.state, c.tone === "ok" ? "check" : c.tone === "warn" ? "alert" : "dash"), el("span", { class: "muted" }, c.detail));
      };
      paint(); off.push(ctx.env.on(paint));
      const actions = el("div", { class: "row" }, ...(ACTIONS[id] || []).map(([label, to], i) => ctx.ui.btn(label, { kind: i === 0 ? "primary" : "default", onClick: () => ctx.nav(to) })));
      root.append(head, el("p", { class: "muted" }, BLURB[id]),
        el("div", { class: "ph-owner", role: "note" }, icon("flask", 16), el("span", {}, `Placeholder screen. The ${w.owner} module will replace it: replacing `, el("code", {}, `static/js/workspaces/${id}.js`), " replaces this screen. The data below is real, read from the backend.")), actions);
      host.replaceChildren(root);
      for (const k of EXTRA_FOR[id]) await EXTRAS[k](ctx, root, params);
    },
    unmount() { off.forEach(f => { try { f(); } catch { /* ignore */ } }); off = []; },
  };
}
export { WORKSPACES };
