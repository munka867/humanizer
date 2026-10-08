// Risk state holder. The shell renders it (top-bar chip + Risk panel). Nothing computes limits yet: until a risk policy is set
// by a future module, everything reads "No risk policy configured" and every usage figure is an em dash with a reason.
// policy shape (all optional): { name, limits:{ max_daily_loss, max_position_value, max_order_value, max_concentration_pct, max_spread_bps }, currency }
// usage  shape (all optional): { daily_loss, position_value, buying_power, concentration_pct, spread_bps, reconciled }
// flags  shape: { stale_data, spread, buying_power, concentration, reconciliation } each {level:'ok'|'warn'|'breach'|null, reason}
export function createRisk() {
  let S = { policy: null, usage: null, flags: {}, asOf: null };
  const subs = new Set();
  const emit = () => subs.forEach(f => { try { f(get()); } catch { /* ignore */ } });
  const get = () => ({ ...S, flags: { ...S.flags } });
  return {
    get,
    /** set({policy, usage, flags, asOf}) merges; pass policy:null to clear. */
    set(p = {}) { S = { ...S, ...p, flags: { ...S.flags, ...(p.flags || {}) }, asOf: p.asOf || new Date().toISOString() }; emit(); },
    on(fn) { subs.add(fn); return () => subs.delete(fn); },
  };
}
