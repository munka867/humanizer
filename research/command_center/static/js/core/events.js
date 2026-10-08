// Event stream: SSE /api/stream with Last-Event-ID resume (manual reconnect + backoff), dedupe by seq, heartbeat-based STALE state.
// States: connecting | live | reconnecting | stale.   Handlers: on(type | '*', fn(event, meta)) -> unsubscribe.
// meta.replay === true for backlog events delivered at start-up (history), false for events that arrive after.
// Delivery: each seq at most once per page session, in ascending order. Handlers must also tolerate events with seq <= their own snapshot().seq.

export function createEvents({ api, staleAfterS = 15, backlog = 500, recentMax = 2000, EventSourceImpl } = {}) {
  const handlers = new Map(); // type -> Set
  const stateSubs = new Set();
  const recentBuf = [];
  const S = { conn: "connecting", lastSeq: 0, lastBeat: null, lastEventAt: null, serverMaxSeq: null, attempts: 0, staleAfterS, error: null, started: false, startMax: 0 };
  let es = null, timer = null, watchdog = null, stopped = true;
  const ES = EventSourceImpl || (typeof EventSource !== "undefined" ? EventSource : null);

  const setConn = (c) => { if (S.conn !== c) { S.conn = c; stateSubs.forEach(f => { try { f({ ...S }); } catch { /* ignore */ } }); } };
  const beat = () => { S.lastBeat = Date.now(); };

  function deliver(e, replay) {
    if (!e || typeof e.seq !== "number" || e.seq <= S.lastSeq) return;      // dedupe by seq
    S.lastSeq = e.seq; S.lastEventAt = Date.now();
    recentBuf.push(e); if (recentBuf.length > recentMax) recentBuf.splice(0, recentBuf.length - recentMax);
    const meta = { replay };
    for (const key of [e.event_type, "*"]) for (const f of handlers.get(key) || []) { try { f(e, meta); } catch (err) { console.error("event handler failed", err); } }
  }

  function connect() {
    if (stopped || !ES) return;
    try { es?.close(); } catch { /* ignore */ }
    const url = `/api/stream?last_event_id=${S.lastSeq}`;
    es = new ES(url);
    es.addEventListener("open", () => { beat(); S.attempts = 0; S.error = null; setConn("live"); });
    es.addEventListener("cc", (m) => {
      beat();
      if (S.conn !== "live") setConn("live");
      let e; try { e = JSON.parse(m.data); } catch { return; }
      deliver(e, e.seq <= S.startMax);
    });
    es.addEventListener("ping", (m) => {
      beat();
      try { const p = JSON.parse(m.data); if (typeof p.max_seq === "number") S.serverMaxSeq = p.max_seq; } catch { /* ignore */ }
      if (S.conn !== "live") setConn("live");
    });
    es.onerror = () => {
      try { es.close(); } catch { /* ignore */ }
      es = null;
      S.error = "stream disconnected";
      if (S.conn !== "stale") setConn("reconnecting");
      const delay = Math.min(8000, 500 * 2 ** Math.min(S.attempts, 4)) + Math.random() * 250;
      S.attempts += 1;
      clearTimeout(timer); timer = setTimeout(connect, delay);
    };
  }

  function watch() {
    clearInterval(watchdog);
    watchdog = setInterval(() => {
      if (stopped || S.lastBeat == null) return;
      if ((Date.now() - S.lastBeat) / 1000 > S.staleAfterS && S.conn !== "stale") setConn("stale");
    }, 1000);
  }

  return {
    /** on(type|'*', fn(event, meta)) -> unsubscribe */
    on(type, fn) { if (!handlers.has(type)) handlers.set(type, new Set()); handlers.get(type).add(fn); return () => handlers.get(type)?.delete(fn); },
    /** onState(fn({conn,lastSeq,lastBeat,...})) -> unsubscribe; also called on every transition. */
    onState(fn) { stateSubs.add(fn); return () => stateSubs.delete(fn); },
    state: () => ({ ...S }),
    /** snapshot({run_id, upto_seq}) -> /api/snapshot (derived agents/tasks/approvals/…); includes `seq`. */
    snapshot(params = {}) {
      const q = new URLSearchParams(); for (const [k, v] of Object.entries(params)) if (v != null) q.set(k, v);
      return api.get("/api/snapshot" + (q.toString() ? "?" + q : ""));
    },
    /** recent(n?, filter?) -> newest-last array of delivered events (ring buffer, includes startup backlog). */
    recent(n = 200, filter) { const r = filter ? recentBuf.filter(filter) : recentBuf; return r.slice(-n); },
    /** inject(event, replay=false): deliver an event locally (dev harness / tests). Same dedupe-by-seq rule as the stream. */
    inject(e, replay = false) { deliver(e, replay); },
    configure(o = {}) { if (o.staleAfterS > 0) S.staleAfterS = o.staleAfterS; },
    async start() {
      if (S.started) return; S.started = true; stopped = false;
      try { const h = await api.get("/api/health"); S.startMax = h.max_seq || 0; S.lastSeq = Math.max(0, S.startMax - backlog); if (h.stale_after_s && staleAfterS === 15) S.staleAfterS = h.stale_after_s; }
      catch { /* server unreachable: connect() will retry */ }
      beat(); watch(); connect();
    },
    stop() { stopped = true; S.started = false; clearTimeout(timer); clearInterval(watchdog); try { es?.close(); } catch { /* ignore */ } es = null; },
  };
}

/** Freshness model for three independent feeds. state in realtime|delayed|historical|stale|unavailable|unknown. */
export const FRESH_STATES = ["realtime", "delayed", "historical", "stale", "unavailable", "unknown"];
export const FEEDS = ["quotes", "broker", "agents"];

export function createFreshness() {
  const feeds = {};
  for (const f of FEEDS) feeds[f] = { feed: f, state: "unknown", asOf: null, source: null, detail: "Not yet checked" };
  const subs = new Set();
  return {
    /** get(feed) -> {feed, state, asOf (ISO|null), source, detail} */
    get(feed) { if (!feeds[feed]) throw new Error(`unknown feed ${feed}`); return { ...feeds[feed] }; },
    all() { return Object.fromEntries(FEEDS.map(f => [f, { ...feeds[f] }])); },
    /** set(feed, state, {asOf, source, detail}) — called by core and by workspaces that own a feed. */
    set(feed, state, { asOf = null, source = null, detail = "" } = {}) {
      if (!feeds[feed]) throw new Error(`unknown feed ${feed}`);
      if (!FRESH_STATES.includes(state)) throw new Error(`bad freshness state ${state}`);
      const cur = feeds[feed];
      if (cur.state === state && cur.asOf === asOf && cur.detail === detail && cur.source === source) return;
      feeds[feed] = { feed, state, asOf, source, detail };
      subs.forEach(f => { try { f(feed, { ...feeds[feed] }); } catch { /* ignore */ } });
    },
    /** on(fn(feed, info)) -> unsubscribe */
    on(fn) { subs.add(fn); return () => subs.delete(fn); },
  };
}
