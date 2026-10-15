// Shared IBKR status store (polls GET /api/ibkr/status). Read-only: nothing here can place, change or cancel an order.
const stores = new WeakMap();

export function ibkrStore(ctx, every = 4000) {
  let s = stores.get(ctx.api);
  if (s) return s;
  let status = null, error = null, timer = null, inflight = false;
  const subs = new Set();
  const emit = () => subs.forEach((f) => { try { f(status, error); } catch { /* ignore */ } });
  async function refresh() {
    if (inflight) return status;
    inflight = true;
    try { status = await ctx.api.get("/api/ibkr/status"); error = null; } catch (e) { error = e.message || "unreachable"; }
    inflight = false; emit(); return status;
  }
  s = {
    get: () => status, error: () => error, refresh,
    connected: () => status?.state === "connected",
    on(fn) {
      subs.add(fn);
      if (!timer) { refresh(); timer = setInterval(refresh, every); }
      else if (status) fn(status, error);
      return () => { subs.delete(fn); if (!subs.size) { clearInterval(timer); timer = null; } };
    },
  };
  stores.set(ctx.api, s);
  return s;
}

export const TYPE_LABEL = { live: "Live", delayed: "Delayed (about 15 min)", frozen: "Frozen (last close)", delayed_frozen: "Delayed frozen", historical: "Historical" };

/** Short human text for the broker-data chip. */
export function chipText(st) {
  if (!st) return { label: "Broker data: checking", tone: "neutral", icon: "plug_off" };
  const port = st.port ? `paper port ${st.port}` : "";
  if (st.fake) {
    if (st.state === "connected") return { label: "SIMULATED IBKR (test fixture)", tone: "demo", icon: "flask" };
    return { label: `SIMULATED IBKR: ${st.state}`, tone: "demo", icon: "flask" };
  }
  if (st.state === "connected") return { label: `Broker data: connected (read-only, ${port})`, tone: "ok", icon: "plug" };
  if (st.state === "connecting") return { label: "Broker data: connecting", tone: "neutral", icon: "clock" };
  if (st.state === "error") return { label: "Broker data: error", tone: "bad", icon: "alert" };
  return { label: "Broker data: not connected", tone: "warn", icon: "plug_off" };
}
