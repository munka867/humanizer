// Hash router: #/<workspace_id>?k=v. Lazy-imports workspaces/<id>.js, unmounts on leave, per-workspace error boundary, loading skeleton.
import { el } from "./dom.js";
import { WORKSPACES, DEFAULT_WORKSPACE, byId } from "./registry.js";

export function parseHash(h = location.hash) {
  const m = /^#\/([a-z_]+)(?:\?(.*))?$/.exec(h || "");
  const id = m && byId(m[1]) ? m[1] : null;
  return { id, raw: m?.[1] || null, params: Object.fromEntries(new URLSearchParams(m?.[2] || "")) };
}

export function createRouter({ ctx, outlet, onRoute, loader = (id) => import(`../workspaces/${id}.js`) }) {
  let current = null;       // {id, mod}
  let seq = 0;

  async function leave() {
    if (!current) return;
    const c = current; current = null;
    try { await c.mod?.unmount?.(); } catch (e) { console.error(`unmount ${c.id} failed`, e); }
    outlet.replaceChildren();
  }

  function errorView(id, err, retry) {
    return el("div", { class: "ws-error", role: "alert" },
      el("h2", {}, `${byId(id)?.title || id} failed to load`),
      el("p", {}, "The rest of the app is unaffected. The error was contained to this workspace."),
      el("pre", { class: "ws-error-msg" }, String(err?.stack || err?.message || err).slice(0, 1200)),
      el("div", { class: "row" }, ctx.ui.btn("Retry", { kind: "primary", onClick: retry }), ctx.ui.btn("Go to Command Center", { onClick: () => ctx.nav(DEFAULT_WORKSPACE) })));
  }

  async function render() {
    const { id, raw, params } = parseHash();
    if (!id) { // unknown / empty route
      if (raw && raw !== DEFAULT_WORKSPACE) { location.replace(`#/${DEFAULT_WORKSPACE}`); return; }
      location.replace(`#/${DEFAULT_WORKSPACE}`); return;
    }
    const mine = ++seq;
    ctx.route = { id, params };
    await leave();
    if (mine !== seq) return;
    onRoute?.(id, params);
    outlet.replaceChildren(ctx.ui.skeleton({ lines: 6, variant: "table" }));
    outlet.setAttribute("aria-busy", "true");
    const host = el("div", { class: "ws-host", dataset: { workspace: id } });
    try {
      const mod = (await loader(id)).default;
      if (mine !== seq) return;
      if (!mod || typeof mod.mount !== "function") throw new Error(`workspaces/${id}.js must export default { id, title, icon, mount(el, ctx), unmount() }`);
      outlet.replaceChildren(host);
      current = { id, mod };
      await mod.mount(host, ctx, params);
    } catch (e) {
      console.error(`workspace ${id} failed`, e);
      if (mine !== seq) return;
      try { await current?.mod?.unmount?.(); } catch { /* ignore */ }
      current = null;
      outlet.replaceChildren(errorView(id, e, () => render()));
    } finally { if (mine === seq) outlet.removeAttribute("aria-busy"); }
  }

  addEventListener("hashchange", render);
  return { render, current: () => current?.id || null, WORKSPACES };
}
