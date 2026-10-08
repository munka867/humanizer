// UI kit. createUI(ctx) -> ctx.ui. Components build DOM with el()/text nodes only (no innerHTML) and use tokens.css variables.
import { el, text, append, clear, uid, trapFocus, focusables, debounce, FOCUSABLE } from "./dom.js";
import { icon } from "./icons.js";
import { fmt, DASH } from "./format.js";

const BADGE_ICON = { ok: "check", warn: "alert", bad: "x_circle", info: "info", demo: "flask", neutral: "dash", accent: "info" };

export function createUI(ctx) {
  const prefs = () => ctx.prefs;

  // ------------------------------------------------------------------ live region / toast
  let liveEl, toastRegion;
  function ensureBody() { return document.body; }
  function live(msg) {
    if (!liveEl) { liveEl = el("div", { class: "sr-only", "aria-live": "polite", "aria-atomic": "true", id: "cc-live" }); ensureBody().append(liveEl); }
    liveEl.textContent = ""; setTimeout(() => { liveEl.textContent = String(msg); }, 30);
  }

  /** toast(message, {kind:'success'|'error'|'info', title, ttl=5000}) — ONLY for real, persisted outcomes (server confirmed). */
  function toast(message, { kind = "success", title, ttl = 5000 } = {}) {
    if (!toastRegion) { toastRegion = el("div", { class: "toast-region", "aria-live": "polite", role: "region", "aria-label": "Notifications" }); ensureBody().append(toastRegion); }
    const t = el("div", { class: ["toast", `toast-${kind}`], role: kind === "error" ? "alert" : "status" },
      icon(kind === "error" ? "x_circle" : kind === "info" ? "info" : "check", 16),
      el("div", { class: "toast-body" }, title ? el("strong", {}, title) : null, el("span", {}, message)),
      el("button", { class: "icon-btn", type: "button", "aria-label": "Dismiss", on: { click: () => t.remove() } }, icon("x", 14)));
    toastRegion.append(t);
    if (ttl > 0) setTimeout(() => t.remove(), kind === "error" ? Math.max(ttl, 9000) : ttl);
    return t;
  }

  // ------------------------------------------------------------------ badge / empty / skeleton / btn
  /** badge(kind, text, iconName?) kind: ok|warn|bad|info|demo|neutral|accent. Always icon + text (never colour-only). */
  function badge(kind, label, iconName) {
    return el("span", { class: ["badge", `badge-${kind}`] }, icon(iconName || BADGE_ICON[kind] || "dash", 14), el("span", { class: "badge-text" }, label));
  }

  /** btn(label, {onClick, icon, kind:'default'|'primary'|'ghost'|'danger', title, disabled, type}) */
  function btn(label, o = {}) {
    const b = el("button", { type: o.type || "button", class: ["btn", o.kind && o.kind !== "default" ? `btn-${o.kind}` : "", o.iconOnly ? "btn-icon" : ""], title: o.title, disabled: o.disabled, "aria-label": o.iconOnly ? label : o.ariaLabel },
      o.icon ? icon(o.icon, 16) : null, o.iconOnly ? null : el("span", {}, label));
    if (o.onClick) b.addEventListener("click", o.onClick);
    return b;
  }

  /** empty(title, why, action?) action: {label, onClick?, href?, kind?} */
  function empty(title, why, action) {
    const box = el("div", { class: "empty", role: "status" }, el("div", { class: "empty-title" }, title), why ? el("p", { class: "empty-why" }, why) : null);
    if (action) {
      if (action.href) box.append(el("a", { class: "btn btn-primary", href: action.href }, action.label));
      else box.append(btn(action.label, { kind: action.kind || "primary", onClick: action.onClick }));
    }
    return box;
  }

  /** skeleton({lines=3, variant:'lines'|'block'|'table', height}) */
  function skeleton({ lines = 3, variant = "lines", height } = {}) {
    const box = el("div", { class: ["skeleton", `skeleton-${variant}`], "aria-busy": "true", role: "status", "aria-label": "Loading" });
    if (variant === "block") box.append(el("div", { class: "sk-line", style: { height: (height || 120) + "px" } }));
    else for (let i = 0; i < (variant === "table" ? Math.max(lines, 5) : lines); i++) box.append(el("div", { class: "sk-line", style: { width: variant === "table" ? "100%" : (96 - ((i * 17) % 40)) + "%" } }));
    return box;
  }

  // ------------------------------------------------------------------ tooltip
  let tipEl, tipTimer;
  function tooltip(target, content, { delay = 350 } = {}) {
    if (!tipEl) { tipEl = el("div", { class: "tooltip", role: "tooltip", id: "cc-tooltip", hidden: true }); ensureBody().append(tipEl); }
    let text_ = content;
    const show = () => {
      const t = typeof text_ === "function" ? text_() : text_; if (!t) return;
      tipEl.textContent = t; tipEl.hidden = false;
      const r = target.getBoundingClientRect(), tr = tipEl.getBoundingClientRect();
      let x = Math.min(Math.max(8, r.left + r.width / 2 - tr.width / 2), innerWidth - tr.width - 8);
      let y = r.bottom + 6; if (y + tr.height > innerHeight - 8) y = r.top - tr.height - 6;
      tipEl.style.left = x + "px"; tipEl.style.top = y + "px";
    };
    const hide = () => { clearTimeout(tipTimer); if (tipEl) tipEl.hidden = true; };
    const enter = () => { clearTimeout(tipTimer); tipTimer = setTimeout(show, delay); };
    const key = (e) => { if (e.key === "Escape") hide(); };
    target.setAttribute("aria-describedby", "cc-tooltip");
    target.addEventListener("mouseenter", enter); target.addEventListener("focus", show); target.addEventListener("mouseleave", hide);
    target.addEventListener("blur", hide); target.addEventListener("keydown", key);
    return { set(t) { text_ = t; }, destroy() { hide(); target.removeEventListener("mouseenter", enter); target.removeEventListener("focus", show); target.removeEventListener("mouseleave", hide); target.removeEventListener("blur", hide); target.removeEventListener("keydown", key); target.removeAttribute("aria-describedby"); } };
  }

  // ------------------------------------------------------------------ popover (anchored, non-modal)
  let openPop = null;
  /** popover(anchor, build(container, api), {placement:'bottom-end'|'bottom-start', label, width, role}) -> {el, close, reposition}. One open at a time. Esc/outside click closes; focus returns to anchor. */
  function popover(anchor, build, o = {}) {
    if (openPop) openPop.close();
    const box = el("div", { class: "popover", role: o.role || "dialog", "aria-label": o.label || "Panel", tabindex: -1, style: o.width ? { width: o.width + "px" } : null });
    const api = { el: box, close, reposition: place };
    function place() {
      const r = anchor.getBoundingClientRect(), b = box.getBoundingClientRect();
      const endAlign = (o.placement || "bottom-end") === "bottom-end";
      let x = endAlign ? r.right - b.width : r.left;
      x = Math.min(Math.max(8, x), Math.max(8, innerWidth - b.width - 8));
      let y = r.bottom + 6; if (y + b.height > innerHeight - 8) y = Math.max(8, r.top - b.height - 6);
      box.style.left = x + "px"; box.style.top = y + "px"; box.style.maxHeight = (innerHeight - 16) + "px";
    }
    function onDown(e) { if (!box.contains(e.target) && !anchor.contains(e.target)) close(false); }
    function onKey(e) { if (e.key === "Escape") { e.stopPropagation(); close(true); } }
    function close(refocus = true) {
      if (!box.isConnected) return;
      document.removeEventListener("pointerdown", onDown, true); document.removeEventListener("keydown", onKey, true); removeEventListener("resize", place);
      box.remove(); anchor.setAttribute("aria-expanded", "false"); if (openPop === api) openPop = null;
      if (refocus) anchor.focus(); o.onClose?.();
    }
    build(box, api);
    ensureBody().append(box); anchor.setAttribute("aria-expanded", "true");
    place(); openPop = api;
    document.addEventListener("pointerdown", onDown, true); document.addEventListener("keydown", onKey, true); addEventListener("resize", place);
    if (o.focus !== false) (focusables(box)[0] || box).focus();
    return api;
  }
  const closePopover = () => openPop?.close(false);

  // ------------------------------------------------------------------ modal layers (drawer / confirm)
  const layers = [];
  function syncInert() {
    const app = document.getElementById("app");
    if (app) app.inert = layers.length > 0;
    layers.forEach((l, i) => { l.root.inert = i !== layers.length - 1; });
  }
  function pushLayer(root, onEsc) {
    const opener = document.activeElement;
    const layer = { root, opener, onEsc };
    layer.release = trapFocus(root);
    layers.push(layer); syncInert();
    return layer;
  }
  function popLayer(layer) {
    layer.release(); const i = layers.indexOf(layer); if (i >= 0) layers.splice(i, 1); syncInert();
    if (layer.opener && layer.opener.isConnected && typeof layer.opener.focus === "function") layer.opener.focus();
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && layers.length && !e.defaultPrevented) { const top = layers[layers.length - 1]; e.preventDefault(); top.onEsc?.(); }
  });

  /** drawer({title, content: Node|fn(bodyEl, api), width=480, label, onClose, footer?: Node}) -> {el, body, close, setTitle}. Right-side modal; focus trapped; Esc/scrim closes; focus returns to opener. */
  function drawer({ title, content, width = 480, label, onClose, footer } = {}) {
    if (openPop) closePopover();
    const tid = uid("drawer-title");
    const body = el("div", { class: "drawer-body" });
    const titleEl = el("h2", { id: tid, class: "drawer-title" }, title || "");
    const closeBtn = el("button", { class: "icon-btn", type: "button", "aria-label": "Close panel", on: { click: () => api.close() } }, icon("x", 16));
    const panel = el("aside", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-labelledby": tid, "aria-label": label, tabindex: -1, style: { "--drawer-w": width + "px" } },
      el("header", { class: "drawer-head" }, titleEl, closeBtn), body, footer ? el("footer", { class: "drawer-foot" }, footer) : null);
    const root = el("div", { class: "layer" }, el("div", { class: "scrim", on: { click: () => api.close() } }), panel);
    let layer, closed = false;
    const api = {
      el: panel, body, setTitle(t) { titleEl.textContent = t; },
      close() { if (closed) return; closed = true; root.classList.remove("open"); const done = () => { root.remove(); popLayer(layer); onClose?.(); }; setTimeout(done, 0); },
    };
    ensureBody().append(root);
    layer = pushLayer(root, () => api.close());
    if (typeof content === "function") content(body, api); else if (content) body.append(content);
    requestAnimationFrame(() => { root.classList.add("open"); (focusables(panel).find(n => n !== closeBtn) || closeBtn).focus(); });
    return api;
  }

  /** confirm({title, scope, body, confirmLabel='Confirm', cancelLabel='Cancel', danger=false, typed}) -> Promise<boolean>.
   *  `scope` states exactly what is affected. `typed` (string) requires typing that string to enable the confirm button. */
  function confirm({ title, scope, body, confirmLabel = "Confirm", cancelLabel = "Cancel", danger = false, typed = null } = {}) {
    if (openPop) closePopover();
    return new Promise((resolve) => {
      const tid = uid("confirm-title"), did = uid("confirm-desc");
      const input = typed ? el("input", { type: "text", class: "input", autocomplete: "off", "aria-label": `Type ${typed} to confirm`, spellcheck: "false" }) : null;
      const ok = el("button", { type: "button", class: ["btn", danger ? "btn-danger" : "btn-primary"], disabled: !!typed }, confirmLabel);
      const cancel = el("button", { type: "button", class: "btn" }, cancelLabel);
      if (input) input.addEventListener("input", () => { ok.disabled = input.value.trim() !== typed; });
      const dlg = el("div", { class: "modal", role: "alertdialog", "aria-modal": "true", "aria-labelledby": tid, "aria-describedby": did, tabindex: -1 },
        el("h2", { id: tid, class: "modal-title" }, title || "Confirm"),
        el("div", { id: did, class: "modal-body" }, body ? el("p", {}, body) : null,
          scope ? el("div", { class: "scope-box" }, el("span", { class: "scope-label" }, "Affects"), el("span", {}, scope)) : null,
          input ? el("label", { class: "field" }, el("span", {}, "Type ", el("code", {}, typed), " to confirm"), input) : null),
        el("div", { class: "modal-actions" }, cancel, ok));
      const root = el("div", { class: "layer layer-modal open" }, el("div", { class: "scrim" }), dlg);
      let layer, done = false;
      const finish = (v) => { if (done) return; done = true; root.remove(); popLayer(layer); resolve(v); };
      cancel.addEventListener("click", () => finish(false)); ok.addEventListener("click", () => finish(true));
      ensureBody().append(root); layer = pushLayer(root, () => finish(false));
      (input || cancel).focus();
    });
  }

  // ------------------------------------------------------------------ tabs
  /** tabs({tabs:[{id,label,render(panelEl, api)}], active, id?, label, onChange}) -> {el, select(id), panel(id), active()}. render() runs on first activation. id => active tab persisted in prefs. */
  function tabs({ tabs: items, active, id, label = "Sections", onChange } = {}) {
    const saved = id ? prefs()?.get(`tabs.${id}`, null) : null;
    let cur = [saved, active, items[0]?.id].find(x => x && items.some(t => t.id === x));
    const list = el("div", { class: "tabs", role: "tablist", "aria-label": label });
    const root = el("div", { class: "tabs-wrap" }, list);
    const btns = {}, panels = {}, rendered = new Set(), uidp = uid("tabs");
    for (const t of items) {
      const b = el("button", { type: "button", role: "tab", class: "tab", id: `${uidp}-t-${t.id}`, "aria-controls": `${uidp}-p-${t.id}`, "aria-selected": "false", tabindex: -1, on: { click: () => select(t.id, true) } }, t.label);
      btns[t.id] = b; list.append(b);
      panels[t.id] = el("div", { class: "tabpanel", role: "tabpanel", id: `${uidp}-p-${t.id}`, "aria-labelledby": b.id, hidden: true, tabindex: 0 });
      root.append(panels[t.id]);
    }
    list.addEventListener("keydown", (e) => {
      const ids = items.map(t => t.id), i = ids.indexOf(cur); let n = null;
      if (e.key === "ArrowRight") n = ids[(i + 1) % ids.length]; else if (e.key === "ArrowLeft") n = ids[(i - 1 + ids.length) % ids.length];
      else if (e.key === "Home") n = ids[0]; else if (e.key === "End") n = ids[ids.length - 1];
      if (n) { e.preventDefault(); select(n, true); btns[n].focus(); }
    });
    const api = { el: root, panel: (i) => panels[i], active: () => cur, select };
    function select(tid, user = false) {
      if (!btns[tid]) return; cur = tid;
      for (const t of items) { const on = t.id === tid; btns[t.id].setAttribute("aria-selected", on); btns[t.id].tabIndex = on ? 0 : -1; panels[t.id].hidden = !on; }
      const t = items.find(x => x.id === tid);
      if (!rendered.has(tid) && t.render) { rendered.add(tid); try { t.render(panels[tid], api); } catch (e) { panels[tid].replaceChildren(el("div", { class: "inline-error", role: "alert" }, `This tab failed to render: ${e.message}`)); } }
      if (user && id) prefs()?.set(`tabs.${id}`, tid);
      onChange?.(tid, user);
    }
    if (cur) select(cur);
    return api;
  }

  // ------------------------------------------------------------------ resizer (side panels)
  /** resizer({panel, side:'left'|'right', id, min=200, max=640, width=320, collapsible=true, label}) -> {handle, collapse(bool), toggle(), setWidth(px), isCollapsed(), width()}.
   *  `side` = which edge of `panel` the handle sits on ('left' for a panel on the right of the layout). The handle is inserted next to the panel; width/collapsed persisted in prefs (panel.<id>). */
  function resizer({ panel, side = "left", id, min = 200, max = 640, width = 320, collapsible = true, label = "Resize panel", insert = true } = {}) {
    const saved = id ? prefs()?.get(`panel.${id}`, null) : null;
    let w = Math.min(max, Math.max(min, saved?.w || width)), collapsed = !!(saved?.collapsed && collapsible);
    const handle = el("div", { class: ["resizer", `resizer-${side}`], role: "separator", "aria-orientation": "vertical", "aria-label": label, tabindex: 0, "aria-valuemin": min, "aria-valuemax": max });
    const apply = () => {
      panel.style.width = collapsed ? "0px" : w + "px"; panel.classList.toggle("collapsed", collapsed); panel.hidden = false;
      panel.toggleAttribute("inert", collapsed); handle.setAttribute("aria-valuenow", collapsed ? 0 : w);
      handle.setAttribute("aria-label", collapsed ? `${label} (collapsed; press Enter to expand)` : `${label} (Enter collapses, arrows resize)`);
      handle.dataset.collapsed = collapsed ? "1" : "0";
    };
    const save = () => { if (id) prefs()?.set(`panel.${id}`, { w, collapsed }); };
    const api = {
      handle, width: () => w, isCollapsed: () => collapsed,
      setWidth(px) { w = Math.min(max, Math.max(min, Math.round(px))); collapsed = false; apply(); save(); },
      collapse(v) { if (!collapsible) return; collapsed = !!v; apply(); save(); },
      toggle() { api.collapse(!collapsed); },
    };
    let startX = 0, startW = 0, dragging = false;
    handle.addEventListener("pointerdown", (e) => { if (e.button !== 0) return; dragging = true; startX = e.clientX; startW = collapsed ? 0 : w; handle.setPointerCapture(e.pointerId); handle.classList.add("dragging"); e.preventDefault(); });
    handle.addEventListener("pointermove", (e) => {
      if (!dragging) return; const dx = (e.clientX - startX) * (side === "left" ? -1 : 1);
      const nw = startW + dx; if (collapsible && nw < min * 0.5) { collapsed = true; } else { collapsed = false; w = Math.min(max, Math.max(min, nw)); } apply();
    });
    const end = () => { if (dragging) { dragging = false; handle.classList.remove("dragging"); save(); } };
    handle.addEventListener("pointerup", end); handle.addEventListener("pointercancel", end);
    handle.addEventListener("dblclick", () => api.toggle());
    handle.addEventListener("keydown", (e) => {
      const step = e.shiftKey ? 48 : 16, dir = side === "left" ? -1 : 1;
      if (e.key === "ArrowLeft") { e.preventDefault(); api.setWidth((collapsed ? 0 : w) + step * (-1) * dir); }
      else if (e.key === "ArrowRight") { e.preventDefault(); api.setWidth((collapsed ? 0 : w) + step * dir); }
      else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); api.toggle(); }
      else if (e.key === "Home") { e.preventDefault(); api.setWidth(min); } else if (e.key === "End") { e.preventDefault(); api.setWidth(max); }
    });
    apply();
    if (insert && panel.parentNode) { if (side === "left") panel.parentNode.insertBefore(handle, panel); else panel.after(handle); }
    return api;
  }

  // ------------------------------------------------------------------ freshness badge
  const FRESH = { realtime: ["ok", "Realtime", "signal"], delayed: ["warn", "Delayed", "clock"], historical: ["info", "Historical", "clock"], stale: ["warn", "Stale", "alert"], unavailable: ["bad", "Unavailable", "plug_off"], unknown: ["neutral", "Unknown", "dash"] };
  /** freshBadge(feed:'quotes'|'broker'|'agents', {showAsOf=true}) -> live-updating badge (icon + text + as-of). */
  function freshBadge(feed, { showAsOf = true } = {}) {
    const host = el("span", { class: "fresh" });
    const draw = () => {
      const f = ctx.freshness.get(feed), [k, lbl, ic] = FRESH[f.state] || FRESH.unknown;
      const asof = showAsOf && f.asOf ? ` · as of ${/^\d{4}-\d{2}-\d{2}$/.test(f.asOf) ? f.asOf : fmt.time(f.asOf, { seconds: false })}` : "";
      host.replaceChildren(badge(k, lbl + asof, ic)); host.title = [f.source, f.detail].filter(Boolean).join(" — ");
    };
    draw();
    const off = ctx.freshness.on((name) => { if (name === feed) { if (!host.isConnected && host.dataset.seen) { off(); return; } draw(); } });
    queueMicrotask(() => setTimeout(() => { host.dataset.seen = host.isConnected ? "1" : ""; }, 50));
    return host;
  }

  // ------------------------------------------------------------------ table
  function table(opts) {
    const o = { pageSize: 500, pageThreshold: 2000, filter: false, exportable: true, columnMenu: true, keyboard: true, ...opts };
    const tid = o.id || null;
    const saved = tid ? (prefs()?.get(`table.${tid}`, null) || {}) : {};
    const state = { w: { ...(saved.w || {}) }, hide: new Set(saved.hide || []), sort: saved.sort && saved.sort.key ? { ...saved.sort } : null, filter: "", page: 0, expanded: new Set() };
    let cols = o.columns.map(c => ({ align: c.align || (c.num ? "num" : "left"), sortable: c.sortable !== false, hideable: c.hideable !== false, width: c.width || 120, minWidth: c.minWidth || 56, ...c }));
    let rows = o.rows || [], view = [];
    const rkey = o.rowKey || ((r, i) => (r && (r.id ?? r.key)) ?? i);
    const persist = () => { if (tid) prefs()?.set(`table.${tid}`, { w: state.w, hide: [...state.hide], sort: state.sort }); };
    const vis = () => cols.filter(c => !state.hide.has(c.key));
    const widthOf = (c) => Math.max(c.minWidth, state.w[c.key] || c.width);
    const getVal = (c, r) => (c.value ? c.value(r) : r?.[c.key]);

    const barCount = el("span", { class: "tbl-count", "aria-live": "polite" });
    const filterIn = o.filter ? el("input", { type: "search", class: "input input-sm", placeholder: "Filter rows", "aria-label": "Filter rows" }) : null;
    const colBtn = o.columnMenu ? btn("Columns", { icon: "columns", onClick: () => openColumnMenu() }) : null;
    if (colBtn) colBtn.setAttribute("aria-haspopup", "dialog");
    const csvBtn = o.exportable ? btn("CSV", { icon: "download", title: "Export the visible columns and filtered rows (all pages) as CSV", onClick: () => exportCSV() }) : null;
    const bar = el("div", { class: "tbl-bar" }, filterIn, barCount, el("span", { class: "spacer" }), colBtn, csvBtn);
    const colgroup = el("colgroup"), thead = el("thead"), tbody = el("tbody");
    const tableEl = el("table", { class: ["tbl-table", o.compact ? "compact" : ""], role: "table" }, o.caption ? el("caption", { class: "sr-only" }, o.caption) : null, colgroup, thead, tbody);
    const scroller = el("div", { class: "tbl-scroll", tabindex: 0, role: "region", "aria-label": o.ariaLabel || o.caption || "Data table", style: o.maxHeight ? { maxHeight: o.maxHeight } : null }, tableEl);
    const pager = el("div", { class: "tbl-pager", hidden: true });
    const root = el("div", { class: "tbl", "data-table": tid || "" }, bar, scroller, pager);
    if (!o.filter && !o.columnMenu && !o.exportable) bar.hidden = true;

    function cmp(a, b, c) {
      const va = c.sortValue ? c.sortValue(a) : getVal(c, a), vb = c.sortValue ? c.sortValue(b) : getVal(c, b);
      const ma = va == null || va === "" || (typeof va === "number" && Number.isNaN(va)), mb = vb == null || vb === "" || (typeof vb === "number" && Number.isNaN(vb));
      if (ma || mb) return ma === mb ? 0 : ma ? 1 : -1;  // missing always last
      if (typeof va === "number" && typeof vb === "number") return va - vb;
      return String(va).localeCompare(String(vb), undefined, { numeric: true, sensitivity: "base" });
    }
    function compute() {
      let v = rows;
      if (state.filter) { const f = state.filter.toLowerCase(); v = v.filter(r => cols.some(c => { const x = c.csv ? c.csv(r) : getVal(c, r); return x != null && String(x).toLowerCase().includes(f); })); }
      if (state.sort) { const c = cols.find(x => x.key === state.sort.key); if (c) { const d = state.sort.dir === "desc" ? -1 : 1; v = [...v].sort((a, b) => { const ma = (c.sortValue ? c.sortValue(a) : getVal(c, a)), mb = (c.sortValue ? c.sortValue(b) : getVal(c, b)); const miss = (x) => x == null || x === ""; if (miss(ma) || miss(mb)) return miss(ma) === miss(mb) ? 0 : miss(ma) ? 1 : -1; return d * cmp(a, b, c); }); } }
      view = v;
    }
    const paged = () => view.length > o.pageThreshold;

    function renderHead() {
      const vc = vis(); colgroup.replaceChildren(...vc.map(c => el("col", { style: { width: widthOf(c) + "px" }, dataset: { col: c.key } })));
      tableEl.style.minWidth = (o.expand ? 28 : 0) + vc.reduce((s, c) => s + widthOf(c), 0) + "px";
      if (o.expand) colgroup.prepend(el("col", { style: { width: "28px" } }));
      const tr = el("tr");
      if (o.expand) tr.append(el("th", { class: "th-expander", scope: "col" }, el("span", { class: "sr-only" }, "Expand")));
      for (const c of vc) {
        const sorted = state.sort?.key === c.key ? state.sort.dir : null;
        const th = el("th", { scope: "col", class: [`al-${c.align}`, c.sortable ? "sortable" : ""], "aria-sort": sorted ? (sorted === "asc" ? "ascending" : "descending") : (c.sortable ? "none" : null), title: c.title });
        const label = c.sortable
          ? el("button", { type: "button", class: "th-btn", on: { click: () => cycleSort(c.key) } }, el("span", { class: "th-label" }, c.label), el("span", { class: "sort-ind", "aria-hidden": "true" }, sorted === "asc" ? "▲" : sorted === "desc" ? "▼" : ""))
          : el("span", { class: "th-btn static" }, el("span", { class: "th-label" }, c.label));
        th.append(label);
        const grip = el("span", { class: "th-grip", role: "separator", "aria-orientation": "vertical", "aria-label": `Resize column ${c.label}`, tabindex: 0 });
        grip.addEventListener("pointerdown", (e) => startResize(e, c, grip)); grip.addEventListener("keydown", (e) => {
          if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); setColWidth(c, widthOf(c) + (e.key === "ArrowRight" ? 12 : -12), true); }
        });
        th.append(grip); tr.append(th);
      }
      thead.replaceChildren(tr);
    }
    function setColWidth(c, px, save) {
      state.w[c.key] = Math.max(c.minWidth, Math.round(px));
      const colEl = colgroup.querySelector(`col[data-col="${CSS.escape(c.key)}"]`); if (colEl) colEl.style.width = state.w[c.key] + "px";
      tableEl.style.minWidth = (o.expand ? 28 : 0) + vis().reduce((s, x) => s + widthOf(x), 0) + "px"; if (save) persist();
    }
    function startResize(e, c, grip) {
      e.preventDefault(); e.stopPropagation(); grip.setPointerCapture(e.pointerId); const x0 = e.clientX, w0 = widthOf(c); grip.classList.add("dragging");
      const mv = (ev) => setColWidth(c, w0 + ev.clientX - x0, false);
      const up = () => { grip.removeEventListener("pointermove", mv); grip.removeEventListener("pointerup", up); grip.removeEventListener("pointercancel", up); grip.classList.remove("dragging"); persist(); };
      grip.addEventListener("pointermove", mv); grip.addEventListener("pointerup", up); grip.addEventListener("pointercancel", up);
    }
    function cycleSort(key) {
      const cur = state.sort?.key === key ? state.sort.dir : null;
      state.sort = cur === null ? { key, dir: "asc" } : cur === "asc" ? { key, dir: "desc" } : null; state.page = 0; persist(); renderAll();
    }

    function cellFor(c, r, i) {
      const td = el("td", { class: `al-${c.align}` });
      let content;
      if (c.render) content = c.render(r, i);
      else {
        const v = getVal(c, r);
        const reason = typeof c.missing === "function" ? c.missing(r) : c.missing;
        content = c.format ? fmt.cell(v, c.format, { reason, opts: c.formatOpts, colour: c.colour }) : (v == null || v === "" ? fmt.missing(reason) : String(v));
      }
      if (content == null || content === "") content = fmt.missing(typeof c.missing === "function" ? c.missing(r) : c.missing);
      td.append(content instanceof Node ? content : document.createTextNode(String(content)));
      return td;
    }
    function renderBody() {
      const vc = vis(), span = vc.length + (o.expand ? 1 : 0);
      const start = paged() ? state.page * o.pageSize : 0, slice = paged() ? view.slice(start, start + o.pageSize) : view;
      const frag = document.createDocumentFragment();
      if (!view.length) {
        const why = rows.length && state.filter ? `No rows match “${state.filter}”.` : o.emptyWhy;
        frag.append(el("tr", { class: "tbl-empty" }, el("td", { colspan: span }, empty(rows.length && state.filter ? "No matching rows" : (o.emptyTitle || "No rows"), why, rows.length ? null : o.emptyAction))));
      }
      slice.forEach((r, idx) => {
        const key = String(rkey(r, start + idx)), isOpen = o.expand && state.expanded.has(key);
        const tr = el("tr", { class: ["tbl-row", isOpen ? "open" : "", o.onRowClick || o.expand ? "clickable" : ""], tabindex: idx === 0 ? 0 : -1, dataset: { key }, "aria-expanded": o.expand ? String(!!isOpen) : null });
        if (o.expand) tr.append(el("td", { class: "td-expander", "aria-hidden": "true" }, icon(isOpen ? "chevron_down" : "chevron_right", 14)));
        for (const c of vc) tr.append(cellFor(c, r, start + idx));
        frag.append(tr);
        if (isOpen) { const holder = el("td", { colspan: span }); frag.append(el("tr", { class: "tbl-expand" }, holder)); try { o.expand(r, holder); } catch (e) { holder.append(el("div", { class: "inline-error", role: "alert" }, `Detail failed: ${e.message}`)); } }
        tr._row = r;
      });
      tbody.replaceChildren(frag);
      barCount.textContent = state.filter || view.length !== rows.length ? `${view.length} of ${rows.length} rows` : `${rows.length} row${rows.length === 1 ? "" : "s"}`;
      renderPager();
    }
    function renderPager() {
      if (!paged()) { pager.hidden = true; return; }
      const pages = Math.ceil(view.length / o.pageSize); state.page = Math.min(state.page, pages - 1);
      pager.hidden = false;
      pager.replaceChildren(btn("Previous", { icon: "chevron_left", disabled: state.page === 0, onClick: () => { state.page--; renderBody(); scroller.scrollTop = 0; } }),
        el("span", { class: "tbl-page" }, `Page ${state.page + 1} of ${pages} · rows ${state.page * o.pageSize + 1}–${Math.min(view.length, (state.page + 1) * o.pageSize)} of ${view.length}`),
        btn("Next", { icon: "chevron_right", disabled: state.page >= pages - 1, onClick: () => { state.page++; renderBody(); scroller.scrollTop = 0; } }));
    }
    function renderAll() { compute(); renderHead(); renderBody(); }

    function toggleRow(tr) {
      if (!o.expand || !tr._row) return; const k = tr.dataset.key;
      state.expanded.has(k) ? state.expanded.delete(k) : state.expanded.add(k);
      const idx = [...tbody.querySelectorAll(".tbl-row")].indexOf(tr); renderBody();
      const rowsNow = tbody.querySelectorAll(".tbl-row"); const t = rowsNow[idx] || rowsNow[0]; if (t) { rowsNow.forEach(x => (x.tabIndex = -1)); t.tabIndex = 0; t.focus(); }
    }
    tbody.addEventListener("click", (e) => {
      const tr = e.target.closest("tr.tbl-row"); if (!tr || e.target.closest("a, button, input, select, textarea, label")) return;
      toggleRow(tr); o.onRowClick?.(tr._row, tr);
    });
    tbody.addEventListener("keydown", (e) => {
      if (!o.keyboard) return; const tr = e.target.closest("tr.tbl-row"); if (!tr || e.target !== tr) return;
      const all = [...tbody.querySelectorAll(".tbl-row")], i = all.indexOf(tr); let n = null;
      if (e.key === "ArrowDown") n = all[Math.min(all.length - 1, i + 1)]; else if (e.key === "ArrowUp") n = all[Math.max(0, i - 1)];
      else if (e.key === "Home") n = all[0]; else if (e.key === "End") n = all[all.length - 1];
      else if (e.key === "PageDown") n = all[Math.min(all.length - 1, i + 10)]; else if (e.key === "PageUp") n = all[Math.max(0, i - 10)];
      else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleRow(tr); o.onRowActivate?.(tr._row, tr); return; }
      else if (e.key === "Escape" && o.expand && state.expanded.has(tr.dataset.key)) { toggleRow(tr); return; }
      if (n) { e.preventDefault(); all.forEach(x => (x.tabIndex = -1)); n.tabIndex = 0; n.focus(); }
    });
    if (filterIn) filterIn.addEventListener("input", debounce(() => { state.filter = filterIn.value.trim(); state.page = 0; compute(); renderBody(); }, 150));

    function openColumnMenu() {
      popover(colBtn, (box) => {
        box.classList.add("menu-pop"); box.append(el("div", { class: "pop-title" }, "Columns"));
        for (const c of cols) {
          const cb = el("input", { type: "checkbox", checked: !state.hide.has(c.key), disabled: !c.hideable });
          cb.addEventListener("change", () => { cb.checked ? state.hide.delete(c.key) : state.hide.add(c.key); if (!vis().length) { state.hide.delete(c.key); cb.checked = true; } persist(); renderAll(); });
          box.append(el("label", { class: "check-row" }, cb, el("span", {}, c.label)));
        }
        box.append(btn("Reset columns", { kind: "ghost", onClick: () => { state.w = {}; state.hide.clear(); persist(); renderAll(); } }));
      }, { label: "Choose columns", placement: "bottom-end" });
    }

    function csvEscape(v) {
      if (v == null) return "";
      let s = typeof v === "number" ? String(v) : String(v);
      if (typeof v !== "number" && /^[=+\-@\t\r]/.test(s)) s = "'" + s;   // spreadsheet formula-injection guard for text cells
      return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    }
    /** toCSV() -> CSV text of the currently visible columns and filtered/sorted rows (all pages). */
    function toCSV() {
      const vc = vis(); const lines = [vc.map(c => csvEscape(c.label)).join(",")];
      for (const r of view) lines.push(vc.map(c => csvEscape(c.csv ? c.csv(r) : getVal(c, r))).join(","));
      return lines.join("\r\n") + "\r\n";
    }
    function exportCSV() {
      const blob = new Blob(["﻿", toCSV()], { type: "text/csv;charset=utf-8" }), url = URL.createObjectURL(blob);
      const a = el("a", { href: url, download: `${o.exportName || tid || "table"}-${new Date().toISOString().slice(0, 10)}.csv` });
      document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 2000);
    }

    renderAll();
    return {
      el: root, toCSV, exportCSV,
      setRows(r) { rows = r || []; state.page = 0; renderAll(); },
      setColumns(c) { cols = c.map(x => ({ align: x.align || "left", sortable: x.sortable !== false, hideable: x.hideable !== false, width: x.width || 120, minWidth: x.minWidth || 56, ...x })); renderAll(); },
      update() { renderAll(); },
      sort(key, dir = "asc") { state.sort = key ? { key, dir } : null; persist(); renderAll(); },
      rows: () => rows, visibleRows: () => view, visibleColumns: () => vis().map(c => c.key),
      setFilter(t) { state.filter = t || ""; if (filterIn) filterIn.value = state.filter; renderAll(); },
      destroy() { closePopover(); root.remove(); },
    };
  }

  return { el, text, append, clear, icon, badge, btn, empty, skeleton, toast, confirm, drawer, tabs, table, resizer, tooltip, popover, closePopover, freshBadge, live,
    inlineError: (container, msg) => { container.replaceChildren(el("div", { class: "inline-error", role: "alert" }, typeof msg === "string" ? msg : msg?.message || "Error")); return container; },
    DASH };
}
