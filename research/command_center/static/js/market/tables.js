// Portfolio tables + metric cards. All empty today: there is no broker adapter. Unknown is an em dash with a reason, never zero.
import { el } from "../core/dom.js";
import { fmt } from "../core/format.js";
import { DEFINITIONS } from "./util.js";

const NB = "unavailable: no broker adapter implemented";
const m = (reason) => reason || NB;

export const ORDER_STATUS = {
  working: ["info", "Working", "clock", "Accepted and resting at the broker; nothing filled yet."],
  acknowledged: ["neutral", "Acknowledged", "info", "The broker acknowledged receipt. An acknowledgement is NOT a fill."],
  partial: ["warn", "Partially filled", "alert", "Some quantity has filled; the remainder is still working."],
  pending_cancel: ["warn", "Pending cancel", "clock", "A cancel was requested; the order may still fill until the broker confirms."],
  rejected: ["bad", "Rejected", "x_circle", "The broker or exchange refused the order."],
  uncertain: ["warn", "Uncertain", "alert", "Broker state could not be confirmed; treat as possibly live until reconciled."],
  filled: ["ok", "Filled", "check", "Fully executed."],
  cancelled: ["neutral", "Cancelled", "x", "Cancelled; no further fills."],
};
export function orderBadge(ctx, status) { const [k, l, i, why] = ORDER_STATUS[status] || ["neutral", String(status || "Unknown"), "dash", "Unrecognised status"]; const b = ctx.ui.badge(k, l, i); b.title = why; return b; }

export function positionColumns(ctx) {
  return [
    { key: "symbol", label: "Symbol", width: 90, render: (r) => el("b", {}, r.symbol) }, { key: "exchange", label: "Exchange · conId", width: 140, value: (r) => `${r.exchange} · ${r.conid}` },
    { key: "shares", label: "Shares", align: "num", width: 80, format: "qty" }, { key: "avg_cost", label: "Avg cost", align: "num", width: 90, format: "price" },
    { key: "mark", label: "Mark", align: "num", width: 90, format: "price", title: "Last daily close from the connector, with its as-of date. No FX conversion; shown in instrument currency." },
    { key: "market_value", label: "Market value", align: "num", width: 110, format: "money" },
    { key: "unrealized", label: "Unrealized P&L", align: "num", width: 120, format: "money", formatOpts: { sign: true }, colour: true }, { key: "realized", label: "Realized P&L", align: "num", width: 110, format: "money", formatOpts: { sign: true }, colour: true },
    { key: "daily_pnl", label: "Daily P&L", align: "num", width: 100, format: "money", formatOpts: { sign: true }, colour: true }, { key: "weight", label: "Weight", align: "num", width: 80, format: "pct", formatOpts: { sign: false } },
    { key: "ownership", label: "Owner", width: 100, missing: "Manual vs strategy ownership is not recorded" }, { key: "currency", label: "Ccy", width: 56 },
  ].map((c) => ({ missing: m(), ...c }));
}
export function orderColumns(ctx) {
  return [
    { key: "symbol", label: "Symbol", width: 90, render: (r) => el("b", {}, r.symbol) }, { key: "side", label: "Side", width: 64 }, { key: "qty", label: "Qty", align: "num", width: 70, format: "qty" },
    { key: "filled", label: "Filled", align: "num", width: 70, format: "qty" }, { key: "remaining", label: "Remaining", align: "num", width: 90, format: "qty" }, { key: "type", label: "Type", width: 80 },
    { key: "limit", label: "Limit", align: "num", width: 80, format: "price" }, { key: "stop", label: "Stop", align: "num", width: 80, format: "price" }, { key: "tif", label: "TIF", width: 60 },
    { key: "outside_rth", label: "Outside RTH", width: 100, value: (r) => (r.outside_rth == null ? null : r.outside_rth ? "yes" : "no") },
    { key: "status", label: "Status", width: 160, render: (r) => orderBadge(ctx, r.status) }, { key: "strategy", label: "Strategy", width: 110, missing: "Manual order or not recorded" },
    { key: "broker_id", label: "Broker id", width: 110 }, { key: "client_id", label: "Client id", width: 110 },
  ].map((c) => ({ missing: m(), ...c }));
}
export function fillColumns(ctx) {
  return [
    { key: "time", label: "Time (ET)", width: 170, render: (r) => fmt.timeEl(r.time) }, { key: "symbol", label: "Symbol", width: 90, render: (r) => el("b", {}, r.symbol) }, { key: "side", label: "Side", width: 64 },
    { key: "qty", label: "Qty", align: "num", width: 70, format: "qty" }, { key: "price", label: "Price", align: "num", width: 90, format: "price" }, { key: "commission", label: "Commission", align: "num", width: 100, format: "money" },
    { key: "order_id", label: "Order id", width: 110 }, { key: "exec_id", label: "Execution id", width: 130 }, { key: "strategy", label: "Strategy", width: 110, missing: "Manual or not recorded" }, { key: "currency", label: "Ccy", width: 56 },
  ].map((c) => ({ missing: m(), ...c }));
}

/** Row expansion: every field of the row as a key/value list (safe text only). */
export function expandRow(row, td) {
  td.append(el("dl", { class: "kv mk-expand" }, Object.entries(row).filter(([, v]) => v == null || typeof v !== "object").map(([k, v]) => el("div", { class: "kv-row" }, el("dt", {}, k), el("dd", {}, v == null ? "—" : String(v))))));
}

export function makeTable(ctx, kind, rows, reason, { action, maxHeight, compact = true, exportable = true, columnMenu = true } = {}) {
  const spec = { positions: [positionColumns, "positions", "No positions"], orders: [orderColumns, "working-orders", "No working orders"], fills: [fillColumns, "fills", "No fills"] }[kind];
  const [colFn, id, title] = spec;
  return ctx.ui.table({ id: `portfolio.${id}`, ariaLabel: title, caption: title, columns: colFn(ctx), rows: rows || [], rowKey: (r, i) => r.id || r.order_id || r.exec_id || `${r.conid}:${r.exchange}:${i}`, compact, maxHeight, filter: false, exportable, columnMenu, exportName: id,
    expand: expandRow, emptyTitle: `${title}: ${reason ? "unavailable" : "none"}`, emptyWhy: reason || NB, emptyAction: action });
}

/** Five summary metrics from /api/portfolio. value null => em dash + reason. One-line reason, definition tooltip, as-of. */
export function metricCards(ctx, pf) {
  const defs = [["equity", "Account equity", "Net liquidation"], ["daily_pnl", "Today's trading P&L", null], ["buying_power", "Buying power", null], ["exposure", "Exposure", null], ["risk_usage", "Risk-limit usage", null]];
  const policy = ctx.risk.get().policy;
  return defs.map(([k, label, sub]) => {
    const mt = pf?.metrics?.[k] || { value: null, reason: pf ? NB : "Portfolio data failed to load", currency: null, as_of: null };
    let val = mt.value;
    const isPct = k === "exposure" || k === "risk_usage";
    const valueEl = val == null ? fmt.missing(mt.reason || NB) : el("span", { class: [k === "daily_pnl" ? fmt.dir(val) : ""] }, isPct ? fmt.pct(val, { sign: false }) : fmt.money(val, { currency: mt.currency || "USD", sign: k === "daily_pnl" }));
    const info = el("span", { class: "mk-info", tabindex: 0, role: "note", "aria-label": `${label}: ${DEFINITIONS[k]}` }, ctx.ui.icon("info", 13)); ctx.ui.tooltip(info, DEFINITIONS[k]);
    const reason = val == null ? (k === "risk_usage" && !policy ? "No approved risk policy; broker not connected" : "Broker not connected") : null;
    return el("div", { class: "mk-metric", dataset: { metric: k, state: val == null ? "unavailable" : "ok" } },
      el("div", { class: "mk-m-label" }, el("span", { class: "mk-m-name" }, label), info, el("span", { class: "spacer" }), el("span", { class: "mk-m-asof" }, `as of ${mt.as_of ? fmt.time(mt.as_of) : "—"}`)),
      el("div", { class: "mk-m-value num" }, valueEl),
      el("div", { class: "mk-m-reason", title: [mt.reason, sub].filter(Boolean).join(" · ") }, reason || [mt.currency, sub].filter(Boolean).join(" · ")));
  });
}
