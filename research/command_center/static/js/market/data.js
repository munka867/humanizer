// Data access for the MARKET workspaces: instruments, quotes, watchlists, portfolio. Read-only except watchlist writes.
import { keyOf } from "./util.js";

export async function loadInstruments(ctx, q = "") {
  const r = await ctx.api.get(`/api/instruments?limit=200${q ? "&q=" + encodeURIComponent(q) : ""}`);
  const list = (r.instruments || []).filter((i) => i.con_id != null && i.exchange).map((i) => ({
    symbol: i.symbol, conid: i.con_id, exchange: i.exchange, currency: i.currency, currencyBasis: i.currency_basis, name: i.name, data: i.data || {}, key: `${i.con_id}:${i.exchange}`,
  }));
  return { instruments: list, reason: r.reason || null };
}

/** Quotes for instruments -> {byKey: {key: quote}, errors: {key: msg}}; uses SYMBOL:EXCHANGE so duplicate tickers resolve. */
export async function loadQuotes(ctx, items) {
  const byKey = {}, errors = {};
  for (let i = 0; i < items.length; i += 20) {
    const chunk = items.slice(i, i + 20);
    try {
      const r = await ctx.api.get(`/api/quotes?symbols=${encodeURIComponent(chunk.map((x) => `${x.symbol}:${x.exchange}`).join(","))}`);
      for (const q of r.quotes || []) byKey[keyOf(q)] = q;
      for (const e of r.errors || []) { const it = chunk.find((x) => x.symbol === e.symbol && (!e.exchange || x.exchange === e.exchange)); if (it) errors[keyOf(it)] = e.error + (e.reason ? `: ${e.reason}` : ""); }
    } catch (e) { for (const it of chunk) errors[keyOf(it)] = e.message; }
  }
  return { byKey, errors };
}

export async function loadWatchlists(ctx) {
  const r = await ctx.api.get("/api/watchlists");
  return { lists: r.watchlists || [], available: r.available_instruments || [], note: r.note };
}
export const loadPortfolio = (ctx) => ctx.api.get("/api/portfolio");
export const loadSignals = (ctx) => ctx.api.get("/api/signals");
export const loadMarkers = (ctx, ins) => ctx.api.get(`/api/markers?symbol=${encodeURIComponent(ins.symbol)}&exchange=${encodeURIComponent(ins.exchange)}&conid=${ins.conid}`);
export const loadBars = (ctx, ins, interval) => ctx.api.get(`/api/bars?symbol=${encodeURIComponent(ins.symbol)}&exchange=${encodeURIComponent(ins.exchange)}&conid=${ins.conid}&interval=${interval}`);

/** Selected instrument: URL params (?symbol&conid&ex) win, then pref, then first available. */
export function pickSelected(ctx, instruments, params, firstOfList = null) {
  const byKey = new Map(instruments.map((i) => [i.key, i]));
  if (params?.conid && params?.ex && byKey.has(`${params.conid}:${params.ex}`)) return byKey.get(`${params.conid}:${params.ex}`);
  if (params?.symbol) { const m = instruments.filter((i) => i.symbol === String(params.symbol).toUpperCase()); if (m.length === 1) return m[0]; }
  const saved = ctx.prefs.get("market.selected", null);
  if (saved && byKey.has(saved)) return byKey.get(saved);
  if (firstOfList && byKey.has(firstOfList)) return byKey.get(firstOfList);
  return instruments[0] || null;
}
export const saveSelected = (ctx, ins) => { try { ctx.prefs.set("market.selected", ins.key); } catch { /* prefs rejected: keep in memory only */ } };
