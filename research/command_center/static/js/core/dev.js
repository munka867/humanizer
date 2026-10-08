// Dev harness: mounts ONE workspace with a STUB ctx (no backend writes, in-memory prefs). See core/README.md.
//   /static/dev.html?ws=stocks            mount workspaces/stocks.js
//   /static/dev.html?ws=stocks&live=1     stub ctx but GET requests go to the real backend (read-only data), POSTs stay stubbed
// Console: window.__dev = { ctx, fixtures, mount(id), emit(event), setFresh(feed, state, opts) }
import { createCtx } from "./ctx.js";
import { WORKSPACES } from "./registry.js";
import { el } from "./dom.js";

const qs = new URLSearchParams(location.search);
const fixtures = {
  "/api/instruments": { ok: true, available: true, total: 2, instruments: [
    { key: "756733:ARCA", symbol: "SPY", con_id: 756733, exchange: "ARCA", currency: "USD", name: null, data: { n_bars: 1000, last: "2026-10-07 13:30:00+00:00", retrieved_on: "2026-10-08" } },
    { key: "320227571:NASDAQ", symbol: "QQQ", con_id: 320227571, exchange: "NASDAQ", currency: "USD", name: null, data: { n_bars: 999, last: "2026-10-07 13:30:00+00:00", retrieved_on: "2026-10-08" } }] },
  "/api/connections": { ok: true, broker: { state: "not_connected", reason: "not_connected: no broker adapter implemented", execution: "none" },
    market_data: { state: "historical", source: "IBKR connector daily-bar files (STUB)", interval: "1d", instruments: 2, last_retrieved_on: "2026-10-08", last_bar: "2026-10-07 13:30:00+00:00", delayed_seconds: 900 },
    event_stream: { max_seq: 0 }, storage: { events_db: "[redacted]" } },
  "/api/snapshot": { seq: 0, event_count: 0, mode: "BACKTEST", has_demo: false, approvals: [], agents: [] },
  "/api/docs": { items: [] }, "/api/results": { items: [] }, "/api/audit": { items: [] }, "/api/prefs": { prefs: {} },
};
if (qs.get("live")) {
  const real = await import("./api.js");
  const live = real.createApi();
  const stubGet = null; void stubGet;
  fixtures.__live = live;
}
const ctx = createCtx({ stub: { fixtures } });
if (fixtures.__live) { const g = fixtures.__live.get; ctx.api.get = (p, o) => g(p, o); }
ctx.env.start();
let current = null, n = 0;
const outlet = document.getElementById("workspace");
async function mount(id) {
  try { await current?.unmount?.(); } catch (e) { console.error(e); }
  outlet.replaceChildren(); current = null;
  const host = el("div", { class: "ws-host", dataset: { workspace: id } }); outlet.append(host);
  try { const mod = (await import(`../workspaces/${id}.js`)).default; current = mod; await mod.mount(host, ctx, Object.fromEntries(qs)); }
  catch (e) { console.error(e); host.append(el("pre", { class: "ws-error-msg" }, String(e.stack || e))); }
}
const sel = el("select", { class: "input", style: { width: "240px" }, "aria-label": "Workspace" }, ...WORKSPACES.map(w => el("option", { value: w.id, selected: w.id === (qs.get("ws") || "command_center") }, `${w.id} — ${w.title}`)));
sel.addEventListener("change", () => { history.replaceState(null, "", `?ws=${sel.value}`); mount(sel.value); });
const emit = (e) => ctx.events.inject({ seq: ++n, event_id: `dev-${n}`, timestamp_utc: new Date().toISOString(), run_id: "dev", agent_id: null, source: "dev", payload: {}, ...e });
document.getElementById("devbar").append(el("strong", {}, "DEV HARNESS (stub ctx)"), sel,
  ctx.ui.btn("Emit test incident", { onClick: () => emit({ event_type: "incident", payload: { severity: "info", summary: "dev incident" } }) }),
  ctx.ui.btn("Theme", { onClick: () => { document.documentElement.dataset.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; } }),
  el("span", { class: "muted" }, "GETs resolve from window.__dev.fixtures; POSTs resolve {ok:true, stub:true}; prefs are in memory."));
window.__dev = { ctx, fixtures, mount, emit, setFresh: (f, s, o) => ctx.freshness.set(f, s, o) };
mount(sel.value);
