// Portfolio & Orders (MARKET worker): read-only views of positions, working orders and fills. There is no broker adapter,
// so everything is empty with a stated reason. NO order entry: the card below explains the prerequisites; there is no submit control.
import { el } from "../core/dom.js";
import { fmt } from "../core/format.js";
import { ensureCss } from "../market/util.js";
import { loadPortfolio } from "../market/data.js";
import { metricCards, makeTable } from "../market/tables.js";

export default {
  id: "portfolio", title: "Portfolio & Orders", icon: "portfolio",
  async mount(host, ctx) {
    await ensureCss();
    const ui = ctx.ui;
    host.classList.add("mk-pf");
    host.replaceChildren(ui.skeleton({ lines: 4 }));
    let pf = null, err = null;
    try { pf = await loadPortfolio(ctx); } catch (e) { err = e.message; }
    const why = pf?.unavailable_reason || (err ? `Portfolio data failed to load: ${err}` : "unavailable: no broker adapter implemented");
    const action = { label: "Connect broker", onClick: () => ctx.nav("data") };
    const sect = (id, title, sub, body) => el("section", { class: "mk-panel mk-pf-sec", id: `pf-${id}`, "aria-labelledby": `pf-${id}-h` }, el("div", { class: "mk-panel-head" }, el("h3", { id: `pf-${id}-h` }, title), el("span", { class: "muted mk-fine" }, sub)), body);
    const b = pf?.broker;
    const head = el("div", { class: "mk-strip", "data-testid": "broker-strip" }, ui.badge("warn", b ? `Broker ${String(b.state).replace(/_/g, " ")}` : "Portfolio data unavailable", "plug_off"),
      el("span", { class: "muted mk-strip-text", title: pf?.note || "" }, b ? `${b.reason.charAt(0).toUpperCase()}${b.reason.slice(1)}. Account figures are unavailable, not zero.` : "Could not load /api/portfolio."), el("span", { class: "spacer" }), ui.btn("Connect broker", { onClick: () => ctx.nav("data") }));
    const prereq = ["A broker adapter (paper first) that is implemented, reviewed and tested: none exists.", "An approved risk policy (max loss per day, max position, max order value, concentration): none is approved.",
      "A deterministic risk engine that enforces those limits in code, with a kill switch and tests: not built.", "Order-state reconciliation that never treats an acknowledgement as a fill: not built.", "Explicit approval to enable paper trading for the connected account (live trading is not permitted)."];
    const entry = el("section", { class: "mk-panel mk-entry", "aria-labelledby": "pf-entry-h" }, el("div", { class: "mk-panel-head" }, el("h3", { id: "pf-entry-h" }, "Order entry unavailable: no broker adapter, no approved risk policy")),
      el("p", { class: "muted" }, "This screen is read-only. No ticket exists, and nothing in this app can place, modify or cancel an order. Required before any ticket can be built:"),
      el("ol", { class: "mk-prereq" }, ...prereq.map((t) => el("li", {}, t))));
    const notes = el("section", { class: "mk-panel mk-notes", "aria-labelledby": "pf-notes-h" }, el("div", { class: "mk-panel-head" }, el("h3", { id: "pf-notes-h" }, "How values will be computed")),
      el("ul", { class: "mk-notelist muted" }, el("li", {}, "Marks: last daily close from the IBKR connector with its as-of date; there is no streaming price. Stale marks will be flagged."),
        el("li", {}, "FX: positions are shown in their own currency. No FX conversion is applied; a base-currency total will state the rate and its time."),
        el("li", {}, "Deposits and withdrawals are tracked separately from trading P&L. That tracking is not implemented yet."),
        el("li", {}, "Unknown values are an em dash with the reason, never zero.")));
    const tbl = (k, rows) => makeTable(ctx, k, rows, pf ? why : why, { action, compact: true, maxHeight: 260 }).el;
    host.replaceChildren(head, el("div", { class: "mk-metrics", role: "group", "aria-label": "Account summary" }, ...metricCards(ctx, pf)),
      el("div", { class: "mk-pf-grid" },
        el("div", { class: "mk-pf-main" },
          sect("positions", "Positions", "stocks only; shares, cost, mark, P&L, weight, owner", tbl("positions", pf?.positions)),
          sect("orders", "Working orders", "an acknowledgement is not a fill", tbl("orders", pf?.working_orders)),
          sect("fills", "Fills", "executions reported by the broker", tbl("fills", pf?.fills))),
        el("div", { class: "mk-pf-side" }, entry, notes)));
  },
  unmount() {},
};
