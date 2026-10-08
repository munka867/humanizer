// Stocks & Charts (MARKET worker): chart (daily/weekly/monthly), instrument facts, data-availability list, watchlist manager.
// Stocks only. Daily bars only. No order submission path exists in this module.
import { el } from "../core/dom.js";
import { fmt } from "../core/format.js";
import { ensureCss, kv, UNAVAIL } from "../market/util.js";
import { createChartPanel } from "../market/chartpanel.js";
import { createWatchState, createWatchManager } from "../market/watchlist.js";
import { loadInstruments, pickSelected, saveSelected } from "../market/data.js";

let dispose = [];
const AVAIL = ["Splits", "Dividends", "Earnings", "News", "Fundamentals", "Level 2 / depth", "Short borrow"];

export default {
  id: "stocks", title: "Stocks & Charts", icon: "stocks",
  async mount(host, ctx, params = {}) {
    await ensureCss();
    const ui = ctx.ui;
    host.classList.add("mk-stk");
    const S = createWatchState(ctx);
    let selected = null, universe = [];
    const details = el("div", { class: "mk-details" }), notice = el("div", { class: "mk-notice", "aria-live": "polite" });
    const chartP = createChartPanel(ctx, { compact: false });
    const avail = el("section", { class: "mk-sec", "aria-label": "Data availability" }, el("h3", { class: "mk-h3" }, "Data availability"),
      el("ul", { class: "mk-avail" }, ...AVAIL.map((n) => el("li", { dataset: { avail: n } }, el("span", { class: "mk-av-n" }, ui.icon("dash", 12), n), el("span", { class: "muted mk-fine" }, UNAVAIL)))),
      el("p", { class: "muted mk-fine" }, "Widgets for these are omitted rather than faked. Company names are unavailable for the same reason."));
    const aside = el("aside", { class: "mk-side mk-side-right mk-stk-aside", "aria-label": "Instrument details" }, el("div", { class: "mk-side-in" }, el("section", { class: "mk-sec" }, el("h3", { class: "mk-h3" }, "Instrument"), details), avail));
    const topRow = el("div", { class: "mk-stk-top" }, el("div", { class: "mk-center" }, chartP.el), aside);
    const rz = ui.resizer({ panel: aside, side: "left", id: "stocks.side", min: 240, max: 460, width: 290, label: "Resize instrument panel" });
    let mgr = null;
    const toggle = ui.btn("Details", { icon: "info", kind: "ghost", title: "Show or hide the details panel", onClick: () => rz.toggle() });
    chartP.el.querySelector(".mk-ctop").append(toggle);

    function drawDetails() {
      const i = selected; if (!i) return details.replaceChildren(el("p", { class: "muted" }, "No instrument selected."));
      const d = i.data || {};
      details.replaceChildren(kv([["Symbol", i.symbol], ["Name", fmt.missing("Company name unavailable: no entitled source connected")], ["Exchange", i.exchange], ["IBKR conId", String(i.conid)], ["Currency", i.currency ? `${i.currency}${i.currencyBasis ? "" : ""}` : fmt.missing("Not stated in the data manifest")],
        ["Daily bars", d.n_bars != null ? fmt.qty(d.n_bars) : "—"], ["First bar", d.first ? String(d.first).slice(0, 10) : "—"], ["Last bar", d.last ? String(d.last).slice(0, 10) : "—"], ["Retrieved", d.retrieved_on || "—"], ["Feed delay", d.delayed_seconds != null ? `${d.delayed_seconds}s` : "—"], ["Adjusted", d.adjusted_for_dividends === false ? "No (unadjusted for dividends)" : d.adjusted_for_dividends ? "Yes" : "—"]]),
        i.currencyBasis ? el("p", { class: "muted mk-fine" }, `Currency ${i.currencyBasis}.`) : null);
    }
    function select(i, push = true) {
      if (!i) return; selected = i; saveSelected(ctx, i); notice.replaceChildren(); drawDetails(); chartP.setInstrument(i).catch(() => {}); mgr?.markSelected();
      if (push) { try { history.replaceState(null, "", `#/stocks?symbol=${encodeURIComponent(i.symbol)}&conid=${i.conid}&ex=${encodeURIComponent(i.exchange)}`); } catch { /* ignore */ } }
    }
    mgr = createWatchManager(ctx, S, { onSelect: (r) => select(universe.find((u) => u.key === r.key) || r), getSelected: () => selected });
    host.replaceChildren(notice, topRow, mgr.el);

    const loading = S.load();
    try {
      await loading;
      universe = (await loadInstruments(ctx)).instruments;
      const q = params.symbol && !params.conid ? universe.filter((u) => u.symbol === String(params.symbol).toUpperCase()) : [];
      if (q.length > 1) {
        notice.replaceChildren(el("div", { class: "mk-pad" }, el("b", {}, `Several instruments share the ticker ${params.symbol}. Choose by exchange and conId: `), ...q.map((u) => ui.btn(`${u.symbol} · ${u.exchange} · ${u.conid}`, { onClick: () => select(u) }))));
        selected = null; drawDetails();
      } else select(pickSelected(ctx, universe, params, S.rows()[0]?.key), false);
    } catch (e) { chartP.el.append(ui.empty("Instruments unavailable", e.message, { label: "Retry", kind: "default", onClick: () => ctx.nav("stocks") })); }
    dispose.push(() => mgr?.destroy(), () => chartP.destroy());
  },
  unmount() { dispose.forEach((f) => { try { f(); } catch { /* ignore */ } }); dispose = []; },
};
