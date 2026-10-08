// API client. GET needs no token (loopback). POST adds X-CC-Token from the Token box / localStorage.
import { store, text, el } from "./dom.js";

const TOKEN_KEY = "cc.token";

export class ApiError extends Error {
  constructor(message, { status = 0, code = "error", body = null, path = "" } = {}) {
    super(message); this.name = "ApiError"; this.status = status; this.code = code; this.body = body; this.path = path;
  }
}

export function createApi({ base = "", timeoutMs = 15000 } = {}) {
  let token = store.get(TOKEN_KEY, "") || "";
  const tokenSubs = new Set();

  async function request(method, path, body, opts = {}) {
    if (typeof path !== "string" || !path.startsWith("/")) throw new ApiError("path must start with /", { code: "bad_path", path });
    const headers = { Accept: "application/json" };
    if (method === "POST") {
      if (!token) {
        const err = new ApiError("API token not set. Enter it in the top bar (Token) to save changes.", { status: 401, code: "no_token", path });
        showInline(opts.errorEl, err); throw err;
      }
      headers["Content-Type"] = "application/json"; headers["X-CC-Token"] = token;
    }
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), opts.timeout || timeoutMs);
    if (opts.signal) opts.signal.addEventListener("abort", () => ctl.abort(), { once: true });
    try {
      const r = await fetch(base + path, { method, headers, body: method === "POST" ? JSON.stringify(body ?? {}) : undefined, signal: ctl.signal, cache: "no-store" });
      const raw = await r.text();
      let data = null;
      try { data = raw ? JSON.parse(raw) : null; } catch { data = null; }
      if (!r.ok) {
        const msg = (data && (data.error || data.message)) || `${r.status} ${r.statusText || "request failed"}`;
        throw new ApiError(msg, { status: r.status, code: r.status === 401 ? "bad_token" : r.status === 404 ? "not_found" : "http", body: data, path });
      }
      return opts.raw ? raw : data;
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(e.name === "AbortError" ? `Request timed out (${path})` : `Network error (${path}): ${e.message}`, { code: e.name === "AbortError" ? "timeout" : "network", path });
      if (method === "POST") showInline(opts.errorEl, err);
      throw err;
    } finally { clearTimeout(to); }
  }

  function showInline(target, err) {
    if (!target) return;
    target.replaceChildren(el("div", { class: "inline-error", role: "alert" }, el("strong", {}, "Not saved. "), err.message));
  }

  return {
    /** get(path, {signal, timeout, raw}) -> parsed JSON; rejects with ApiError. */
    get: (path, opts) => request("GET", path, null, opts),
    /** post(path, body, {errorEl}) -> parsed JSON; adds X-CC-Token; failure is rendered inline into opts.errorEl (if given) AND rejected. */
    post: (path, body, opts) => request("POST", path, body, opts),
    token: {
      get: () => token,
      has: () => !!token,
      set(v) { token = String(v || "").trim(); if (token) store.set(TOKEN_KEY, token); else store.del(TOKEN_KEY); tokenSubs.forEach(f => { try { f(!!token); } catch { /* ignore */ } }); },
      clear() { this.set(""); },
      onChange(fn) { tokenSubs.add(fn); return () => tokenSubs.delete(fn); },
    },
    clearInline(target) { if (target) target.replaceChildren(); },
    showInline,
  };
}

/** Render an error inline (role=alert) into container; for failures that are not api.post calls. */
export function inlineError(container, message) {
  container.replaceChildren(el("div", { class: "inline-error", role: "alert" }, typeof message === "string" ? message : message?.message || "Error"));
  return container;
}
export { text };
