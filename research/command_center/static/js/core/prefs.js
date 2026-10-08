// Preferences: server-persisted (/api/prefs). localStorage is ONLY a UI cache (instant first paint, offline read, retry queue for
// writes the server has not yet accepted), never the source of truth: when the server has a value and nothing is pending, the server wins.
import { store, debounce, safeJSON } from "./dom.js";

const KEY_RE = /^[a-z0-9_.-]{1,64}$/;
const CACHE_KEY = "cc.prefs.cache";
const DIRTY_KEY = "cc.prefs.dirty";
const MAX_BYTES = 64 * 1024;
const DEL = { $del: 1 };

export function createPrefs({ api, memoryOnly = false } = {}) {
  let data = memoryOnly ? {} : (safeJSON(store.get(CACHE_KEY, "{}"), {}) || {});
  const dirty = new Map(Object.entries(memoryOnly ? {} : (safeJSON(store.get(DIRTY_KEY, "{}"), {}) || {}))); // key -> value | DEL
  const subs = new Set();
  const status = { synced: memoryOnly || dirty.size === 0, loaded: false, pending: dirty.size, error: null, lastSync: null, serverBacked: !memoryOnly };
  const isDel = (v) => v && typeof v === "object" && v.$del === 1 && Object.keys(v).length === 1;

  const emit = (key) => { subs.forEach(f => { try { f(key, data[key]); } catch { /* ignore */ } }); };
  const persistLocal = () => { if (memoryOnly) return; store.set(CACHE_KEY, JSON.stringify(data)); store.set(DIRTY_KEY, JSON.stringify(Object.fromEntries(dirty))); };

  async function flush() {
    if (memoryOnly || !dirty.size) return { ok: true };
    const sets = {}, dels = [];
    for (const [k, v] of dirty) { if (isDel(v)) dels.push(k); else sets[k] = v; }
    try {
      await api.post("/api/prefs", { prefs: sets, delete: dels });
      for (const k of Object.keys(sets)) if (dirty.get(k) === sets[k]) dirty.delete(k);
      for (const k of dels) if (isDel(dirty.get(k))) dirty.delete(k);
      status.pending = dirty.size; status.synced = !dirty.size; status.error = null; status.lastSync = Date.now(); persistLocal();
      emit("*"); return { ok: true };
    } catch (e) {
      status.synced = false; status.error = e.message; status.pending = dirty.size; emit("*");
      return { ok: false, error: e.message, code: e.code };
    }
  }
  const flushSoon = debounce(flush, 400);

  return {
    /** load(): read server prefs once at boot. Server values win except for keys with unsynced local edits (those are re-sent). */
    async load() {
      if (memoryOnly) { status.loaded = true; return status; }
      try {
        const r = await api.get("/api/prefs");
        const merged = { ...(r.prefs || {}) };
        for (const [k, v] of dirty) { if (isDel(v)) delete merged[k]; else merged[k] = v; }
        data = merged; status.error = null;
        status.synced = !dirty.size; persistLocal();
        if (dirty.size && api.token.has()) flushSoon();
      } catch (e) { status.error = `server prefs unavailable (${e.message}); using local cache`; status.synced = false; }
      status.loaded = true; status.pending = dirty.size; emit("*");
      return status;
    },
    /** get(key, dflt): synchronous read of the in-memory copy. */
    get(key, dflt) { return Object.prototype.hasOwnProperty.call(data, key) ? data[key] : dflt; },
    /** set(key, val): JSON value (<=64KB). Memory + UI cache update immediately; persisted to the server in the background (needs the API token; without it the edit stays 'unsynced' and is retried when a token is set). */
    set(key, val) {
      if (!KEY_RE.test(key)) throw new Error(`prefs key must match ${KEY_RE}`);
      const blob = JSON.stringify(val === undefined ? null : val);
      if (blob.length > MAX_BYTES) throw new Error("prefs value too large (max 64KB)");
      data[key] = JSON.parse(blob); dirty.set(key, data[key]); status.synced = false; status.pending = dirty.size; persistLocal();
      emit(key); flushSoon();
    },
    remove(key) { delete data[key]; dirty.set(key, DEL); status.synced = false; status.pending = dirty.size; persistLocal(); emit(key); flushSoon(); },
    /** onChange(fn(key, value)) -> unsubscribe. key '*' = load/sync status change. */
    onChange(fn) { subs.add(fn); return () => subs.delete(fn); },
    status: () => ({ ...status }),
    flush: () => { flushSoon.cancel(); return flush(); },
    all: () => ({ ...data }),
  };
}
