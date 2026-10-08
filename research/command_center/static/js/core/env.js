// ctx.env: execution environment, display mode, broker state, per-workspace capability states. Everything is derived from real endpoints.
// There is NO execution in this app: the environment is always "research only". The mode is a display label and never changes execution.
import { WORKSPACES } from "./registry.js";

export const MODES = [
  { id: "DEMO", selectable: true, desc: "Show seeded demo events (source='demo'), clearly labelled." },
  { id: "BACKTEST", selectable: true, desc: "Research on historical daily bars; nothing is executed." },
  { id: "REPLAY", selectable: true, desc: "Replay recorded events with original timestamps." },
  { id: "SHADOW", selectable: false, refusal: "Unavailable: SHADOW needs a live market-data feed and a shadow-order recorder. Neither exists yet.", desc: "Record hypothetical orders against live quotes." },
  { id: "PAPER", selectable: false, refusal: "Refused: PAPER needs an approved broker adapter and a deterministic risk engine. Neither exists yet.", desc: "Simulated orders at a broker." },
  { id: "LIVE", selectable: false, refusal: "Refused: LIVE trading is not permitted. No broker adapter exists.", desc: "Real orders." },
];
const BROKER_DEFAULT = { state: "not_connected", reason: "not_connected: no broker adapter implemented", adapter: null, execution: "none", setup_complete: false };

export function createEnv({ api, events, freshness, prefs, audit, risk }) {
  const subs = new Set();
  const S = { conn: null, connError: null, docs: null, results: null, snap: null, snapError: null, loadedAt: null };
  const emit = () => subs.forEach(f => { try { f(); } catch { /* ignore */ } });
  let timer = null;

  const validMode = (m) => MODES.some(x => x.id === m && x.selectable);
  function currentMode() {
    const p = prefs.get("ui.mode", null); if (validMode(p)) return p;
    const s = S.snap?.mode; if (validMode(s)) return s;
    return "BACKTEST";
  }

  async function refresh() {
    const [c, d, r, s] = await Promise.allSettled([api.get("/api/connections"), api.get("/api/docs"), api.get("/api/results"), api.get("/api/snapshot")]);
    if (c.status === "fulfilled") { S.conn = c.value; S.connError = null; } else { S.connError = c.reason?.message || "unreachable"; }
    S.docs = d.status === "fulfilled" ? (d.value.items || []).length : null;
    S.results = r.status === "fulfilled" ? (r.value.items || []).length : null;
    if (s.status === "fulfilled") { S.snap = { event_count: s.value.event_count, mode: s.value.mode, has_demo: s.value.has_demo, approvals: s.value.approvals || [], seq: s.value.seq }; S.snapError = null; } else S.snapError = s.reason?.message || "unreachable";
    S.loadedAt = Date.now();
    syncFreshness(); emit();
  }

  function syncFreshness() {
    const md = S.conn?.market_data;
    if (!S.conn) freshness.set("quotes", "unknown", { detail: `Could not read /api/connections (${S.connError || "not loaded"})`, source: "IBKR connector daily bars" });
    else freshness.set("quotes", md.state, { asOf: md.last_bar ? String(md.last_bar).slice(0, 10) : null, source: md.source,
      detail: md.state === "unavailable" ? (md.reason || "No daily-bar files") : `Daily bars only, no streaming quotes. Retrieved ${md.last_retrieved_on || "?"}; source delay ${Array.isArray(md.delayed_seconds) ? "varies" : md.delayed_seconds ?? "?"} s.` });
    const b = S.conn?.broker || BROKER_DEFAULT;
    freshness.set("broker", b.state === "connected" ? "unknown" : "unavailable", { source: "Broker adapter", detail: b.reason || "not connected" });
    syncAgents();
  }
  function syncAgents() {
    const e = events.state();
    const asOf = e.lastBeat ? new Date(e.lastBeat).toISOString() : null;
    const map = { live: "realtime", reconnecting: "delayed", stale: "stale", connecting: "unknown" };
    const detail = { live: "Event stream connected (heartbeat every 5 s)", reconnecting: "Event stream disconnected; retrying with backoff", stale: `No heartbeat for over ${e.staleAfterS} s`, connecting: "Connecting to the event stream" }[e.conn];
    freshness.set("agents", map[e.conn] || "unknown", { asOf, source: "Event stream /api/stream", detail });
  }

  const caps = () => {
    const nInst = S.conn?.market_data?.instruments;
    const bro = S.conn?.broker || BROKER_DEFAULT;
    const noData = { state: "needs data", tone: "warn", detail: "No IBKR daily-bar files found. Open Data & Connections." };
    const dataCap = nInst > 0 ? { state: "ready", tone: "ok", detail: `${nInst} instruments with local daily bars (historical, not streaming).` } : S.conn ? noData : { state: "checking", tone: "neutral", detail: S.connError ? "Backend unreachable" : "Checking" };
    return {
      command_center: dataCap, stocks: dataCap,
      portfolio: bro.state === "connected" ? { state: "ready", tone: "ok", detail: "Broker connected" } : { state: "no broker", tone: "warn", detail: `${bro.reason}. Positions and orders cannot load.` },
      agents: S.snap ? (S.snap.event_count > 0 ? { state: "ready", tone: "ok", detail: `${S.snap.event_count} events recorded in the latest run` } : { state: "no events yet", tone: "warn", detail: "No agent events have been posted. Use emit.py or POST /api/events." }) : { state: "checking", tone: "neutral", detail: S.snapError || "Checking" },
      research: S.docs == null ? { state: "checking", tone: "neutral", detail: "Checking" } : S.docs > 0 ? { state: "ready", tone: "ok", detail: `${S.docs} research documents indexed` } : { state: "no docs", tone: "warn", detail: "No documents in research/docs" },
      backtest: S.results == null ? { state: "checking", tone: "neutral", detail: "Checking" } : S.results > 0 ? { state: "ready", tone: "ok", detail: `${S.results} result files` } : { state: "no results yet", tone: "warn", detail: "No result files in research/results/daily" },
      data: { state: "ready", tone: "ok", detail: "Connections and data sources" },
      settings: { state: "ready", tone: "ok", detail: "Preferences, audit log" },
    };
  };

  const env = {
    MODES,
    get mode() { return currentMode(); },
    /** Always 'none': there is no execution path in this app. */
    execution: "none",
    environment: { id: "research", label: "RESEARCH ONLY", detail: "Execution: none — broker adapter not built" },
    readOnly: true,
    get broker() { return S.conn?.broker || BROKER_DEFAULT; },
    get hasDemo() { return !!S.snap?.has_demo; },
    get connections() { return S.conn; },
    get approvalsPending() { return (S.snap?.approvals || []).filter(a => !a.resolution); },
    /** setMode(id) -> {ok, reason?, audit:{ok,error?}}. PAPER/LIVE/SHADOW are refused (and the refusal is audited). Selectable modes are display labels only. */
    async setMode(id) {
      const m = MODES.find(x => x.id === id);
      const old = currentMode();
      if (!m) return { ok: false, reason: "Unknown mode" };
      if (!m.selectable) { const a = await audit("ui.mode_refused", { target: "mode", old, new: id, outcome: "refused", reason: m.refusal }); return { ok: false, reason: m.refusal, audit: a }; }
      prefs.set("ui.mode", id); emit();
      const a = old !== id ? await audit("ui.mode", { target: "mode", old, new: id, outcome: "applied", reason: "display label only; no execution exists" }) : { ok: true, skipped: true };
      return { ok: true, audit: a };
    },
    capability: (id) => caps()[id] || { state: "unknown", tone: "neutral", detail: "" },
    capabilities: caps,
    refresh,
    on(fn) { subs.add(fn); return () => subs.delete(fn); },
    notify: emit,
    start() { refresh(); clearInterval(timer); timer = setInterval(() => { syncAgents(); }, 1000); events.onState(() => syncAgents()); setInterval(refresh, 60000); events.on("approval.requested", () => refresh()); events.on("approval.resolved", () => refresh()); },
    workspaces: WORKSPACES,
  };
  return env;
}
