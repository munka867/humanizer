// Shared helpers for the MARKET workspaces. No innerHTML, no external URLs, no order paths.
import { el } from "../core/dom.js";

export const UNAVAIL = "unavailable: no entitled source connected";
export const keyOf = (i) => `${i.conid ?? i.con_id}:${i.exchange}`;

const loaded = {};
/** Inject market.css once; resolves when applied (or after 1.5 s so a slow disk never blocks mount). */
export function ensureCss() {
  if (loaded.css) return loaded.css;
  loaded.css = new Promise((res) => {
    const existing = document.getElementById("mk-css");
    if (existing) return res();
    const l = el("link", { rel: "stylesheet", href: "/static/css/market.css", id: "mk-css" });
    l.addEventListener("load", () => res()); l.addEventListener("error", () => res());
    document.head.append(l); setTimeout(res, 1500);
  });
  return loaded.css;
}
/** Load lightweight-charts (vendored, v5.2.1) by one injected script tag. */
export function loadChartLib() {
  if (window.LightweightCharts) return Promise.resolve(window.LightweightCharts);
  if (loaded.lib) return loaded.lib;
  loaded.lib = new Promise((res, rej) => {
    const s = el("script", { src: "/static/vendor/lightweight-charts.standalone.production.js", id: "mk-lwc" });
    s.addEventListener("load", () => (window.LightweightCharts ? res(window.LightweightCharts) : rej(new Error("chart library loaded but not available"))));
    s.addEventListener("error", () => { loaded.lib = null; rej(new Error("could not load the chart library from /static/vendor")); });
    document.head.append(s);
  });
  return loaded.lib;
}

/** Read a tokens.css variable as a concrete colour string (hex tokens only). */
export const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888888";
export function rgba(hex, a) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex); if (!m) return hex;
  const n = parseInt(m[1], 16); return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`;
}
export const isoDate = (sec) => new Date(sec * 1000).toISOString().slice(0, 10); // daily bars: UTC date == NY session date (13:30/14:30 UTC opens)

/** Simple moving average over closes. Returns [{time, value}] starting at index n-1. Chart-only; never a strategy rule. */
export function sma(bars, n) {
  const out = []; let sum = 0;
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i].close; if (i >= n) sum -= bars[i - n].close;
    if (i >= n - 1) out.push({ time: bars[i].time, value: sum / n });
  }
  return out;
}
/** Range presets in calendar days back from the last bar. */
export const RANGES = [["1M", 30], ["3M", 91], ["6M", 182], ["1Y", 365], ["2Y", 730], ["ALL", null]];

export function quoteFreshness(q) {
  if (!q) return { kind: "neutral", label: "No quote", why: "No quote returned" };
  if (q.stale) return { kind: "warn", label: "Stale", why: q.stale_reason || "older than the freshness limit", icon: "alert" };
  return { kind: "info", label: "Historical", why: `Latest daily bar ${q.as_of}; not a streaming quote`, icon: "clock" };
}

export const DEFINITIONS = {
  equity: "Net liquidation value of the broker account: cash plus market value of positions, in account currency.",
  daily_pnl: "Today's trading profit and loss: realised plus unrealised change since the previous close, excluding deposits and withdrawals.",
  buying_power: "Broker-reported amount available to open new positions, after margin requirements.",
  exposure: "Gross market value of open positions as a share of equity.",
  risk_usage: "Share of the approved risk limits (daily loss, position size, concentration) currently used. Needs an approved risk policy.",
};

/** Tiny labelled key/value list (safe text). */
export function kv(rows) {
  return el("dl", { class: "kv" }, rows.filter(Boolean).map(([k, v]) => el("div", { class: "kv-row" }, el("dt", {}, k), el("dd", {}, v ?? "—"))));
}
export function safeText(v, max = 200) { return v == null ? "" : String(v).slice(0, max); }
