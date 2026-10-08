// ctx: the object every workspace receives. See core/README.md for the full contract.
import { createApi, ApiError } from "./api.js";
import { createEvents, createFreshness } from "./events.js";
import { createPrefs } from "./prefs.js";
import { fmt } from "./format.js";
import { createUI } from "./ui.js";
import { createEnv } from "./env.js";
import { createRisk } from "./risk.js";
import { el, text } from "./dom.js";

const AUDIT_FIELDS = ["target", "old", "new", "outcome", "reason", "detail"];

/** createCtx({stub}) — normal mode talks to the backend. stub: {fixtures: {'/api/path': json|fn}} gives an isolated ctx for dev.html (no backend, in-memory prefs). */
export function createCtx({ stub = null, staleAfterS } = {}) {
  let api = createApi();
  if (stub) {
    const base = api;
    api = { ...base,
      get: async (path) => { const p = path.split("?")[0]; const f = stub.fixtures?.[p]; if (f === undefined) throw new ApiError(`stub ctx: no fixture for ${p}`, { status: 404, code: "not_found", path }); return typeof f === "function" ? f(path) : f; },
      post: async (path, body) => { const f = stub.fixtures?.["POST " + path.split("?")[0]]; return f === undefined ? { ok: true, stub: true } : (typeof f === "function" ? f(body) : f); } };
  }
  const freshness = createFreshness();
  const events = createEvents({ api, staleAfterS });
  const prefs = createPrefs({ api, memoryOnly: !!stub });
  const risk = createRisk();
  const ctx = { api, freshness, events, prefs, fmt, risk, el, text, stub: !!stub, route: { id: null, params: {} }, version: "shell-1" };
  ctx.ui = createUI(ctx);

  /** audit(kind, detail) -> Promise<{ok, seq?, error?}>; never throws. kind 'area.action' (e.g. 'ui.mode'); detail {target, old, new, outcome:'applied'|'refused'|'failed'|'info', reason, detail}. Records an audit.config event (needs the token). */
  ctx.audit = async (kind, d = {}) => {
    try {
      const [area, ...rest] = String(kind).split(".");
      const body = { area, action: rest.join(".") || "change" };
      for (const k of AUDIT_FIELDS) if (d[k] !== undefined) body[k] = d[k];
      const r = await api.post("/api/audit", body);
      return { ok: true, seq: r.seq };
    } catch (e) { return { ok: false, error: e.message, code: e.code }; }
  };
  ctx.env = createEnv({ api, events, freshness, prefs, audit: (k, d) => ctx.audit(k, d), risk });
  /** nav(id, params) sets location.hash; the router mounts the workspace. */
  ctx.nav = (id, params) => {
    const q = params && Object.keys(params).length ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v != null)).toString() : "";
    const h = `#/${id}${q}`;
    if (location.hash === h) dispatchEvent(new HashChangeEvent("hashchange")); else location.hash = h;
  };
  return ctx;
}
