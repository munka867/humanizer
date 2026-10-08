// Watchlists: shared state + compact list (Command Center) + full manager (Stocks & Charts).
// Lists live on the server (market.sqlite3). Items are keyed by conId + exchange, never by ticker alone. Nothing here recommends a stock.
import { el } from "../core/dom.js";
import { fmt } from "../core/format.js";
import { loadInstruments, loadQuotes, loadWatchlists } from "./data.js";
import { keyOf, quoteFreshness } from "./util.js";

export function createWatchState(ctx) {
  const S = { lists: [], available: [], universe: new Map(), activeId: null, quotes: {}, errors: {}, loaded: false, error: null, subs: new Set() };
  const emit = () => S.subs.forEach((f) => f());
  S.on = (f) => { S.subs.add(f); return () => S.subs.delete(f); };
  S.active = () => S.lists.find((l) => l.id === S.activeId) || S.lists[0] || null;
  S.rows = () => (S.active()?.items || []).map((it) => { const k = keyOf(it), u = S.universe.get(k) || {}; return { key: k, symbol: it.symbol, conid: it.conid, exchange: it.exchange, currency: u.currency || null, currencyBasis: u.currencyBasis, name: u.name || null, quote: S.quotes[k] || null, qerr: S.errors[k] || null, ...(u.key ? { data: u.data } : {}) }; });
  S.setActive = (id) => { S.activeId = id; try { ctx.prefs.set("market.activeList", id); } catch { /* keep in memory */ } emit(); S.refreshQuotes(); };
  S.apply = (resp) => { S.lists = resp.watchlists || resp.lists || S.lists; if (resp.available_instruments) S.available = resp.available_instruments; if (!S.lists.some((l) => l.id === S.activeId)) S.activeId = S.lists[0]?.id || null; emit(); S.refreshQuotes(); };
  S.refreshQuotes = async () => {
    const items = S.rows(); if (!items.length) { emit(); return; }
    const need = items.filter((r) => !S.quotes[r.key] && !S.errors[r.key]);
    if (need.length) { const r = await loadQuotes(ctx, need); Object.assign(S.quotes, r.byKey); Object.assign(S.errors, r.errors); }
    emit();
  };
  S.load = async () => {
    try {
      const [w, u] = await Promise.all([loadWatchlists(ctx), loadInstruments(ctx)]);
      S.lists = w.lists; S.available = w.available; S.universe = new Map(u.instruments.map((i) => [i.key, i]));
      const saved = ctx.prefs.get("market.activeList", null); S.activeId = S.lists.some((l) => l.id === saved) ? saved : S.lists[0]?.id || null;
      S.loaded = true; S.error = null;
    } catch (e) { S.error = e.message; S.loaded = true; }
    emit(); await S.refreshQuotes();
  };
  return S;
}

const chgCell = (r, kind) => {
  if (!r.quote) return fmt.missing(r.qerr || "No quote available");
  const v = kind === "pct" ? r.quote.pct_change : r.quote.change;
  return el("span", { class: ["cell", "num", fmt.dir(v)] }, kind === "pct" ? fmt.pct(v) : (v > 0 ? "+" : "") + fmt.num(v, 2));
};

// ---------------------------------------------------------------------------------------------- compact (Command Center)
export function createCompactWatchlist(ctx, S, { onSelect, getSelected, onManage }) {
  const sel = el("select", { class: "input input-sm mk-listsel", "aria-label": "Watchlist" });
  sel.addEventListener("change", () => S.setActive(sel.value));
  const body = el("div", { class: "mk-wl-body", role: "listbox", "aria-label": "Watchlist instruments" });
  const foot = el("div", { class: "mk-wl-foot muted" });
  const root = el("section", { class: "mk-panel mk-wl", "aria-label": "Watchlist" },
    el("div", { class: "mk-panel-head" }, el("h3", {}, "Watchlist"), el("span", { class: "spacer" }), ctx.ui.btn("Manage", { kind: "ghost", onClick: onManage })), sel, body, foot);
  function draw() {
    if (!S.loaded) { body.replaceChildren(ctx.ui.skeleton({ lines: 4 })); return; }
    if (S.error) { body.replaceChildren(ctx.ui.empty("Watchlists unavailable", S.error, { label: "Retry", kind: "default", onClick: () => S.load() })); return; }
    sel.replaceChildren(...S.lists.map((l) => el("option", { value: l.id, selected: l.id === S.active()?.id }, `${l.name} (${l.items.length})`)));
    const rows = S.rows(), cur = getSelected()?.key;
    if (!rows.length) { body.replaceChildren(el("p", { class: "muted mk-pad" }, "This list is empty. Add instruments with data available from Stocks & Charts.")); }
    else body.replaceChildren(...rows.map((r) => {
      const fr = quoteFreshness(r.quote);
      const b = el("button", { type: "button", role: "option", class: "mk-wl-row", "aria-selected": String(r.key === cur), dataset: { key: r.key, symbol: r.symbol },
        title: `${r.symbol} · ${r.exchange} · conId ${r.conid}. ${r.quote ? `Last ${fmt.price(r.quote.last)} (daily close ${r.quote.as_of}); ${fr.why}` : r.qerr || "no quote"}`,
        on: { click: () => onSelect(S.universe.get(r.key) || { ...r, key: r.key }) } },
        el("span", { class: "mk-wl-sym" }, el("b", {}, r.symbol), el("span", { class: "muted mk-wl-ex" }, r.exchange)),
        el("span", { class: "mk-wl-last num" }, r.quote ? fmt.price(r.quote.last) : fmt.missing(r.qerr || "No quote")),
        el("span", { class: ["mk-wl-chg", "num", fmt.dir(r.quote?.pct_change)] }, r.quote ? fmt.pct(r.quote.pct_change) : "—"),
        el("span", { class: ["mk-dot", r.quote?.stale ? "stale" : "hist"], "aria-label": fr.label }));
      return b;
    }));
    const any = rows.find((r) => r.quote);
    foot.replaceChildren(any ? `Daily closes, as of ${any.quote.as_of}. ${any.quote.stale ? "STALE: " + any.quote.stale_reason : "Historical, not streaming."}` : "No quotes available.", el("br"), "Instruments with data available, not recommendations.");
  }
  const off = S.on(draw); draw();
  return { el: root, destroy: off };
}

// ---------------------------------------------------------------------------------------------- manager (Stocks & Charts)
export function createWatchManager(ctx, S, { onSelect, getSelected }) {
  const ui = ctx.ui;
  let sort = ctx.prefs.get("market.wlSort", "manual"), filter = "", mode = null, table, results = [];
  const errBox = el("div", { class: "mk-errbox", "aria-live": "polite" });
  const sel = el("select", { class: "input input-sm mk-listsel", "aria-label": "Watchlist" }); sel.addEventListener("change", () => S.setActive(sel.value));
  const nameIn = el("input", { class: "input input-sm", type: "text", maxlength: 60, "aria-label": "Watchlist name", placeholder: "List name" });
  const nameForm = el("form", { class: "mk-nameform", hidden: true }, nameIn, ui.btn("Save", { kind: "primary", type: "submit" }), ui.btn("Cancel", { onClick: () => setMode(null) }));
  const search = el("input", { class: "input input-sm", type: "search", "aria-label": "Search instruments to add", placeholder: "Add instrument: search symbol", autocomplete: "off" });
  const resBox = el("ul", { class: "mk-results", role: "list", "aria-label": "Search results" });
  const filt = el("input", { class: "input input-sm", type: "search", "aria-label": "Filter this list", placeholder: "Filter list" });
  const sortSel = el("select", { class: "input input-sm", "aria-label": "Sort order" }, ...[["manual", "Manual order"], ["symbol", "Symbol A–Z"], ["chg", "% change high–low"], ["vol", "Volume high–low"]].map(([v, l]) => el("option", { value: v, selected: v === sort }, l)));
  const bRename = ui.btn("Rename", { onClick: () => setMode("rename") }), bDelete = ui.btn("Delete", { onClick: delList }), bNew = ui.btn("New list", { icon: "list", onClick: () => setMode("create") });
  bDelete.classList.add("btn-danger-outline");
  const note = el("p", { class: "muted mk-fine" }, "Lists hold instruments with data available in this app. They are not recommendations, screens or rankings.");
  const tableHost = el("div", { class: "mk-wl-table" });
  const root = el("section", { class: "mk-panel mk-wm", "aria-label": "Watchlist manager" },
    el("div", { class: "mk-panel-head" }, el("h3", {}, "Watchlists"), sel, bNew, bRename, bDelete, nameForm, el("span", { class: "spacer" }), sortSel, filt),
    el("div", { class: "mk-addrow" }, search, el("span", { class: "muted mk-fine" }, "Only instruments with local daily bars can be added.")), resBox, errBox, tableHost, note);

  function setMode(m) { mode = m; nameForm.hidden = !m; if (m) { nameIn.value = m === "rename" ? S.active()?.name || "" : ""; nameIn.focus(); nameIn.select(); } else (m === null && bNew.focus()); }
  nameForm.addEventListener("submit", async (e) => {
    e.preventDefault(); errBox.replaceChildren();
    const body = mode === "create" ? { action: "create", name: nameIn.value } : { action: "rename", id: S.active().id, name: nameIn.value };
    try { const r = await ctx.api.post("/api/watchlists", body, { errorEl: errBox }); S.apply(r); if (mode === "create") S.setActive(r.result.id); ui.toast(mode === "create" ? `Created list "${r.result.name}"` : `Renamed to "${r.result.name}"`); setMode(null); } catch { /* shown inline */ }
  });
  async function delList() {
    const l = S.active(); if (!l) return;
    if (!(await ui.confirm({ title: "Delete watchlist?", scope: `"${l.name}" and its ${l.items.length} item(s). Instruments themselves and their data are not affected.`, confirmLabel: "Delete list", danger: true }))) return;
    try { const r = await ctx.api.post("/api/watchlists", { action: "delete", id: l.id }, { errorEl: errBox }); S.apply(r); ui.toast(`Deleted "${l.name}"`); } catch { /* inline */ }
  }
  const items = (id) => `/api/watchlists/${encodeURIComponent(id)}/items`;
  async function write(body, okMsg) {
    errBox.replaceChildren();
    try { const r = await ctx.api.post(items(S.active().id), body, { errorEl: errBox }); S.apply(r); if (okMsg) ui.live(okMsg); return true; } catch { return false; }
  }
  const ref = (r) => ({ conid: r.conid, exchange: r.exchange });
  async function move(key, delta) {
    const order = S.active().items.map((i) => keyOf(i)), i = order.indexOf(key), j = i + delta; if (i < 0 || j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    if (await write({ action: "reorder", order: order.map((k) => { const [c, ...e] = k.split(":"); return { conid: +c, exchange: e.join(":") }; }) }, "Order saved")) focusRow(key);
  }
  function focusRow(key) { requestAnimationFrame(() => table.el.querySelector(`[data-rowkey="${CSS.escape(key)}"]`)?.focus()); }

  // search -> add
  let st; search.addEventListener("input", () => { clearTimeout(st); st = setTimeout(runSearch, 180); }); search.addEventListener("focus", runSearch);
  async function runSearch() {
    const q = search.value.trim();
    try { results = (await loadInstruments(ctx, q)).instruments.slice(0, 8); } catch (e) { results = []; resBox.replaceChildren(el("li", { class: "muted mk-pad" }, `Search failed: ${e.message}`)); return; }
    const have = new Set((S.active()?.items || []).map(keyOf));
    resBox.replaceChildren(...(results.length ? results.map((r) => el("li", { class: "mk-res" },
      el("span", { class: "mk-res-main" }, el("b", {}, r.symbol), el("span", { class: "muted" }, ` ${r.exchange} · conId ${r.conid} · ${r.currency || "currency unknown"}`)),
      ui.btn(have.has(r.key) ? "Added" : "Add", { disabled: have.has(r.key) || !S.active(), onClick: async () => { if (await write({ action: "add", ...ref(r) }, `${r.symbol} added`)) { ui.toast(`${r.symbol} (${r.exchange}) added to "${S.active().name}"`); runSearch(); } } }))) : [el("li", { class: "muted mk-pad" }, q ? `No instrument matches "${q}". Only instruments with local daily bars are searchable; no company-name search (names unavailable).` : "No instruments with data available.")]));
  }
  document.addEventListener("pointerdown", docDown, true);
  function docDown(e) { if (!root.contains(e.target) || (!resBox.contains(e.target) && e.target !== search && !errBox.contains(e.target) && resBox.childElementCount)) { if (!resBox.contains(e.target) && e.target !== search) resBox.replaceChildren(); } }
  search.addEventListener("keydown", (e) => { if (e.key === "Escape") { resBox.replaceChildren(); } });

  filt.addEventListener("input", () => { filter = filt.value; table?.setFilter(filter); draw(); });
  sortSel.addEventListener("change", () => { sort = sortSel.value; ctx.prefs.set("market.wlSort", sort); draw(); });

  const SORTS = { symbol: (a, b) => a.symbol.localeCompare(b.symbol) || a.exchange.localeCompare(b.exchange), chg: (a, b) => (b.quote?.pct_change ?? -1e9) - (a.quote?.pct_change ?? -1e9), vol: (a, b) => (b.quote?.volume ?? -1) - (a.quote?.volume ?? -1) };
  const canDrag = () => sort === "manual" && !filter;
  const cols = () => [
    { key: "grip", label: "", width: 40, minWidth: 40, sortable: false, hideable: false, render: (r) => { const g = el("span", { class: ["mk-grip", canDrag() ? "" : "off"], draggable: canDrag() ? "true" : null, title: canDrag() ? "Drag to reorder (or use the move buttons)" : "Reordering needs manual order and no filter", "aria-hidden": "true", dataset: { key: r.key } }, "⋮⋮");
        g.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", r.key); e.dataTransfer.effectAllowed = "move"; }); return g; } },
    { key: "symbol", label: "Symbol", width: 96, sortable: false, render: (r) => el("b", { class: "mk-symcell", dataset: { rowkey: r.key }, tabindex: -1 }, r.symbol) },
    { key: "name", label: "Name", width: 130, sortable: false, value: (r) => r.name, missing: "Company name unavailable: no entitled source connected" },
    { key: "exchange", label: "Exchange", width: 90, sortable: false },
    { key: "conid", label: "conId", width: 100, align: "num", sortable: false, format: "qty" },
    { key: "currency", label: "Ccy", width: 56, sortable: false, missing: "Currency not stated in the data manifest" },
    { key: "last", label: "Last", width: 86, align: "num", sortable: false, render: (r) => (r.quote ? fmt.cell(r.quote.last, "price") : fmt.missing(r.qerr || "No quote")) },
    { key: "chg", label: "Change", width: 82, align: "num", sortable: false, render: (r) => chgCell(r, "chg") },
    { key: "pct", label: "% chg", width: 82, align: "num", sortable: false, render: (r) => chgCell(r, "pct") },
    { key: "vol", label: "Volume", width: 92, align: "num", sortable: false, render: (r) => (r.quote ? fmt.cell(r.quote.volume, "compact") : fmt.missing(r.qerr || "No quote")) },
    { key: "fresh", label: "Quote", width: 190, sortable: false, render: (r) => { if (!r.quote) return ui.badge("neutral", "No quote", "dash"); const f = quoteFreshness(r.quote); const b = ui.badge(f.kind, `${f.label} ${r.quote.as_of}`, f.icon); b.title = f.why; return b; } },
    { key: "act", label: "Actions", width: 130, sortable: false, hideable: false, render: (r) => { const n = S.active().items.length, i = S.active().items.findIndex((x) => keyOf(x) === r.key), dis = !canDrag();
        const mk = (lab, aria, fn, d) => { const b = el("button", { type: "button", class: "icon-btn mk-mv", "aria-label": `${aria} ${r.symbol} ${r.exchange}`, title: dis && aria !== "Remove" ? "Needs manual order and no filter" : aria, disabled: d }, lab); b.addEventListener("click", (e) => { e.stopPropagation(); fn(); }); return b; };
        return el("span", { class: "mk-acts" }, mk("↑", "Move up", () => move(r.key, -1), dis || i <= 0), mk("↓", "Move down", () => move(r.key, 1), dis || i >= n - 1), mk("✕", "Remove", () => remove(r))); } },
  ];
  async function remove(r) { if (await write({ action: "remove", ...ref(r) }, `${r.symbol} removed`)) ui.toast(`${r.symbol} (${r.exchange}) removed from "${S.active().name}"`); }

  function markSel() { const cur = getSelected()?.key, vr = table?.visibleRows() || []; table?.el.querySelectorAll("tr.tbl-row").forEach((tr, i) => { tr.dataset.selected = String(vr[i]?.key === cur); tr.dataset.key = vr[i]?.key || ""; }); }
  function draw() {
    sel.replaceChildren(...S.lists.map((l) => el("option", { value: l.id, selected: l.id === S.active()?.id }, `${l.name} (${l.items.length})`)));
    for (const b of [bRename, bDelete]) b.disabled = !S.active();
    let rows = S.rows(); if (SORTS[sort]) rows = [...rows].sort(SORTS[sort]);
    if (!S.loaded) { tableHost.replaceChildren(ui.skeleton({ variant: "table", lines: 4 })); return; }
    if (S.error) { tableHost.replaceChildren(ui.empty("Watchlists unavailable", S.error, { label: "Retry", kind: "default", onClick: () => S.load() })); return; }
    if (!table) {
      table = ui.table({ id: "stocks.watch", ariaLabel: "Watchlist", caption: "Watchlist instruments", rows, columns: cols(), rowKey: (r) => r.key, compact: true, exportName: "watchlist", maxHeight: 300,
        emptyTitle: "This watchlist is empty", emptyWhy: "Search above to add instruments that have daily data available.", onRowClick: (r) => onSelect(S.universe.get(r.key) || r), onRowActivate: (r) => onSelect(S.universe.get(r.key) || r) });
      tableHost.replaceChildren(table.el);
      table.el.addEventListener("dragover", (e) => { if (canDrag()) e.preventDefault(); });
      table.el.addEventListener("drop", (e) => { if (!canDrag()) return; e.preventDefault(); const from = e.dataTransfer.getData("text/plain"), tr = e.target.closest("tr.tbl-row"); if (!from || !tr) return; dropOn(from, tr.dataset.key); });
    } else { table.setColumns(cols()); table.setRows(rows); }
    markSel();
  }
  async function dropOn(fromKey, toKey) {
    if (!toKey || fromKey === toKey) return; const order = S.active().items.map(keyOf), i = order.indexOf(fromKey), j = order.indexOf(toKey); if (i < 0 || j < 0) return;
    order.splice(j, 0, order.splice(i, 1)[0]);
    await write({ action: "reorder", order: order.map((k) => { const [c, ...e] = k.split(":"); return { conid: +c, exchange: e.join(":") }; }) }, "Order saved");
  }
  const off = S.on(draw); draw();
  return { el: root, markSelected: markSel, destroy() { off(); document.removeEventListener("pointerdown", docDown, true); table?.destroy?.(); } };
}
