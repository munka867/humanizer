// Chart panel: candlesticks + volume (lightweight-charts 5.2.1, vendored), interval control, range presets, SMA overlays (chart-only),
// crosshair readout, markers + evidence drawer, keyboard pan/zoom. Daily bars only: intraday is NOT offered (no source).
import { el, text } from "../core/dom.js";
import { fmt } from "../core/format.js";
import { loadBars, loadMarkers, loadQuotes } from "./data.js";
import { loadChartLib, token, rgba, isoDate, sma, RANGES, kv, safeText, quoteFreshness } from "./util.js";

const INTERVALS = [["1D", "Daily"], ["1W", "Weekly"], ["1M", "Monthly"]];
const OVERLAYS = [["sma20", 20, "SMA 20"], ["sma50", 50, "SMA 50"], ["sma200", 200, "SMA 200"]];
const STATE_META = { proposed: ["P", "Proposed"], submitted: ["S", "Submitted"], filled: ["F", "Filled"] };

export function createChartPanel(ctx, { compact = false, extra = null, idPrefix = "mk" } = {}) {
  const ui = ctx.ui;
  let ins = null, quote = null, bars = [], meta = null, limits = null, markers = [], markersReason = null, seq = 0, destroyed = false;
  let interval = ctx.prefs.get("market.interval", "1D"), range = ctx.prefs.get("market.range", "1Y");
  let on = ctx.prefs.get("market.overlays", {}); if (!on || typeof on !== "object") on = {};
  let L, chart, candles, volume, markerApi, sealedS, lines = {}, ro, mo, byTime = new Map(), cleanups = [];

  // ---------------------------------------------------------------- DOM
  const title = el("h2", { class: "mk-ctitle" }), sub = el("span", { class: "mk-csub muted" });
  const intervalBox = el("div", { class: "mk-seg", role: "group", "aria-label": "Bar interval" });
  const rangeBox = el("div", { class: "mk-seg", role: "group", "aria-label": "Visible range" });
  const overlayBox = el("div", { class: "mk-seg", role: "group", "aria-label": "Chart-only overlays" });
  const navBox = el("div", { class: "mk-seg", role: "group", "aria-label": "Pan and zoom" });
  const chips = el("div", { class: "mk-chips", "aria-label": "Data provenance" });
  const readout = el("div", { class: "mk-readout num", "data-testid": "readout", "aria-live": "off" });
  const canvas = el("div", { class: "mk-canvas", tabindex: 0, role: "application", "aria-label": "Price chart. Arrow left and right pan, plus and minus zoom, Home fits all, End jumps to the latest bar." });
  const overlayMsg = el("div", { class: "mk-overlay-msg", hidden: true });
  const legend = el("div", { class: "mk-legend" });
  const mstrip = el("div", { class: "mk-mstrip", "aria-label": "Trade markers" });
  const root = el("section", { class: ["mk-chart", compact ? "compact" : ""], "aria-label": "Price chart" },
    el("div", { class: "mk-ctop" }, el("div", { class: "mk-ctitlebox" }, title, sub), el("span", { class: "spacer" }), navBox, extra),
    el("div", { class: "mk-ctools" }, intervalBox, rangeBox, overlayBox),
    chips,
    el("div", { class: "mk-cwrap" }, readout, canvas, overlayMsg),
    legend, mstrip);

  const btn = (label, o = {}) => { const b = el("button", { type: "button", class: "mk-segbtn", "aria-pressed": o.pressed == null ? null : String(!!o.pressed), "aria-label": o.aria, title: o.title, disabled: o.disabled, dataset: o.dataset }, label); if (o.onClick) b.addEventListener("click", o.onClick); return b; };

  function drawControls() {
    intervalBox.replaceChildren();
    const offered = limits?.intervals || INTERVALS.map((x) => x[0]);
    for (const [v, lab] of INTERVALS) {
      if (!offered.includes(v)) continue;
      intervalBox.append(btn(v, { pressed: v === interval, aria: `${lab} bars`, dataset: { interval: v }, onClick: () => { interval = v; ctx.prefs.set("market.interval", v); drawControls(); fetchBars(); } }));
    }
    const why = limits?.intraday || "unavailable: no intraday history source connected";
    const intra = el("span", { class: "mk-disabled-wrap", tabindex: 0, role: "note", "aria-label": `Intraday intervals unavailable. ${why}` }, btn("Intraday", { disabled: true }));
    ui.tooltip(intra, `Intraday intervals are not offered. ${why}.`);
    intervalBox.append(intra);
    rangeBox.replaceChildren(...RANGES.map(([lab]) => btn(lab, { pressed: lab === range, aria: lab === "ALL" ? "Show all history" : `Show last ${lab}`, dataset: { range: lab }, onClick: () => { range = lab; ctx.prefs.set("market.range", lab); drawControls(); applyRange(); } })));
    const vw = el("span", { class: "mk-disabled-wrap", tabindex: 0, role: "note", "aria-label": "VWAP unavailable on daily bars" }, btn("VWAP", { disabled: true }));
    ui.tooltip(vw, "VWAP is unavailable on daily bars: it needs intraday trades, and no intraday source is connected.");
    overlayBox.title = "Overlays are computed in the browser for viewing only. They are not strategy rules.";
    if (compact) {
      const n = OVERLAYS.filter(([k]) => on[k]).length;
      const ob = btn(`Overlays${n ? ` (${n})` : ""}`, { aria: "Chart-only overlays menu", dataset: { overlays: "menu" }, onClick: (e) => { const a = e.currentTarget; ui.popover(a, (box) => {
        box.append(el("div", { class: "pop-title" }, "Overlays (chart-only)"), el("p", { class: "pop-note" }, "Computed in the browser for viewing. Not strategy rules, never used for signals."));
        for (const [k, , lab] of OVERLAYS) { const cb = el("input", { type: "checkbox", id: `${idPrefix}-ov-${k}`, checked: !!on[k], dataset: { overlay: k } }); cb.addEventListener("change", () => { on[k] = cb.checked; ctx.prefs.set("market.overlays", on); drawControls(); drawOverlays(); }); box.append(el("label", { class: "check-row", for: cb.id }, cb, lab)); }
        box.append(el("div", { class: "check-row muted" }, el("input", { type: "checkbox", disabled: true }), "VWAP: unavailable on daily bars"));
      }, { label: "Overlays", width: 280, placement: "bottom-start" }); } });
      overlayBox.replaceChildren(ob);
    } else {
      overlayBox.replaceChildren(...OVERLAYS.map(([k, , lab]) => btn(lab, { pressed: !!on[k], aria: `${lab} overlay (chart-only, not a strategy rule)`, dataset: { overlay: k }, onClick: () => { on[k] = !on[k]; ctx.prefs.set("market.overlays", on); drawControls(); drawOverlays(); } })), vw, el("span", { class: "mk-note muted" }, "chart-only"));
    }
    navBox.replaceChildren(...[btn("‹", { aria: "Pan left", onClick: () => pan(-1) }), btn("›", { aria: "Pan right", onClick: () => pan(1) }), btn("−", { aria: "Zoom out", onClick: () => zoom(1.25) }),
      btn("+", { aria: "Zoom in", onClick: () => zoom(0.8) }), btn("Fit", { aria: "Fit all bars", onClick: () => chart?.timeScale().fitContent() }), compact ? null : btn("Latest", { aria: "Jump to latest bar", onClick: () => chart?.timeScale().scrollToRealTime() })].filter(Boolean));
  }

  // ---------------------------------------------------------------- chart lifecycle
  const theme = () => ({ text: token("--text-2"), grid: token("--border"), bg: token("--surface"), pos: token("--pos"), neg: token("--neg") });
  function applyTheme() {
    if (!chart) return; const t = theme();
    chart.applyOptions({ layout: { background: { type: "solid", color: t.bg }, textColor: t.text }, grid: { vertLines: { color: rgba(t.grid, 0.45) }, horzLines: { color: rgba(t.grid, 0.45) } },
      rightPriceScale: { borderColor: t.grid }, timeScale: { borderColor: t.grid }, crosshair: { vertLine: { color: t.text, labelBackgroundColor: token("--raised") }, horzLine: { color: t.text, labelBackgroundColor: token("--raised") } } });
    candles.applyOptions({ upColor: t.pos, downColor: t.neg, wickUpColor: t.pos, wickDownColor: t.neg });
    if (bars.length) { volume.setData(volData()); drawSealed(); }
    for (const [k, s] of Object.entries(lines)) s.applyOptions({ color: lineColor(k) });
  }
  const sealedFrom = () => meta?.sealed_final_test?.from || null;
  const inSealed = (t) => !!sealedFrom() && t >= sealedFrom();
  function drawSealed() { const w = token("--warn"); sealedS.setData(sealedFrom() ? bars.filter((b) => inSealed(b.t)).map((b) => ({ time: b.t, value: 1, color: rgba(w, 0.10) })) : []); }
  const lineColor = (k) => ({ sma20: token("--accent"), sma50: token("--warn"), sma200: token("--demo") }[k]);
  function ensureChart() {
    if (chart) return;
    const t = theme();
    chart = L.createChart(canvas, {
      width: canvas.clientWidth || 600, height: canvas.clientHeight || 300, autoSize: false,
      layout: { attributionLogo: true, fontFamily: getComputedStyle(document.body).fontFamily, fontSize: 12, background: { type: "solid", color: t.bg }, textColor: t.text },
      crosshair: { mode: L.CrosshairMode.Normal }, localization: { locale: "en-US" },
      timeScale: { rightOffset: 3, timeVisible: false, secondsVisible: false, fixLeftEdge: false }, handleScale: { axisPressedMouseMove: true }, rightPriceScale: { scaleMargins: { top: compact ? 0.16 : 0.1, bottom: 0.24 } },
    });
    sealedS = chart.addSeries(L.HistogramSeries, { priceScaleId: "sealed", priceLineVisible: false, lastValueVisible: false, base: 0, autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 1 } }) });
    chart.priceScale("sealed").applyOptions({ scaleMargins: { top: 0, bottom: 0 }, visible: false });
    candles = chart.addSeries(L.CandlestickSeries, { borderVisible: false, priceLineVisible: true });
    volume = chart.addSeries(L.HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "vol", priceLineVisible: false, lastValueVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    markerApi = L.createSeriesMarkers(candles, []);
    chart.subscribeCrosshairMove(onCross); chart.subscribeClick(onClick);
    ro = new ResizeObserver(() => { const w = canvas.clientWidth, h = canvas.clientHeight; if (w > 0 && h > 0) chart.applyOptions({ width: w, height: h }); });
    ro.observe(canvas);
    mo = new MutationObserver(applyTheme); mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    applyTheme();
    canvas.__market = { chart, candles, volume, markerApi };
  }
  const volData = () => { const t = theme(); return bars.map((b) => ({ time: b.t, value: b.volume, color: rgba(b.close >= b.open ? t.pos : t.neg, 0.45) })); };

  // ---------------------------------------------------------------- data
  const norm = (b) => ({ ...b, t: isoDate(b.time) });
  async function fetchBars() {
    if (!ins) return; const my = ++seq;
    overlayMsg.hidden = false; overlayMsg.replaceChildren(ui.skeleton({ variant: "block", height: 120 }));
    try {
      const [b, mk, qs] = await Promise.all([loadBars(ctx, ins, interval),
        loadMarkers(ctx, ins).catch((e) => ({ markers: [], reason: `markers unavailable: ${e.message}` })), loadQuotes(ctx, [ins]).catch(() => ({ byKey: {} }))]);
      if (my !== seq || destroyed) return;
      meta = b.meta; limits = b.limits; bars = b.bars.map(norm); markers = mk.markers || []; markersReason = mk.reason || null; quote = qs.byKey?.[ins.key] || null;
      if (!bars.length) throw Object.assign(new Error("No bars returned for this instrument and interval."), { empty: true });
      overlayMsg.hidden = true; ensureChart();
      candles.setData(bars.map((x) => ({ time: x.t, open: x.open, high: x.high, low: x.low, close: x.close })));
      volume.setData(volData()); drawSealed();
      byTime = new Map(bars.map((x, i) => [x.t, i]));
      drawControls(); drawOverlays(); drawMarkers(); drawChips(); applyRange(); showReadout(null);
      canvas.dataset.ready = "1"; canvas.dataset.bars = String(bars.length); canvas.dataset.interval = interval;
    } catch (e) {
      if (my !== seq || destroyed) return;
      bars = []; canvas.dataset.ready = "0";
      overlayMsg.hidden = false;
      overlayMsg.replaceChildren(ui.empty(e.empty ? "No bars to show" : "Chart data unavailable", `${e.message}${e.body?.reason ? " — " + e.body.reason : ""}`, { label: "Retry", kind: "default", onClick: fetchBars }));
      if (candles) { candles.setData([]); volume.setData([]); }
      readout.replaceChildren(); chips.replaceChildren(); drawControls();
    }
  }
  function drawOverlays() {
    if (!chart) return;
    for (const [k, n] of OVERLAYS.map(([k, n]) => [k, n])) {
      if (on[k] && bars.length) {
        if (!lines[k]) lines[k] = chart.addSeries(L.LineSeries, { color: lineColor(k), lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, title: "", priceScaleId: "right" });
        lines[k].setData(sma(bars, n).map((p) => ({ time: isoDate(p.time), value: p.value })));
      } else if (lines[k]) { chart.removeSeries(lines[k]); delete lines[k]; }
    }
  }
  function tradeMarkers() {
    return markers.map((m) => ({ ...m, t: typeof m.time === "number" ? isoDate(m.time) : String(m.time).slice(0, 10) })).filter((m) => byTime.has(m.t) || bars.some((b) => b.t >= m.t)).map((m) => {
      const k = STATE_META[m.state] ? m.state : "proposed", buy = m.side !== "sell", filled = k === "filled";
      const color = k === "filled" ? token(buy ? "--pos" : "--neg") : k === "submitted" ? token("--warn") : token("--accent");
      const snap = bars.find((b) => b.t >= m.t) || bars.at(-1);
      return { time: snap.t, position: buy ? "belowBar" : "aboveBar", color, shape: filled ? (buy ? "arrowUp" : "arrowDown") : k === "submitted" ? "square" : "circle", text: STATE_META[k][0], id: String(m.id ?? ""), _m: m };
    });
  }
  function drawMarkers() {
    const tm = tradeMarkers(), all = tm.map(({ _m, ...x }) => x);
    const last = bars.at(-1);
    if (meta && last?.partial) all.push({ time: last.t, position: "inBar", color: token("--warn"), shape: "circle", text: "partial", id: "partial" });
    all.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
    markerApi.setMarkers(all);
    legend.replaceChildren(el("span", { class: "mk-legend-title" }, "Trade markers"),
      ...Object.entries(STATE_META).map(([k, [g, lab]]) => el("span", { class: ["mk-lg", `mk-lg-${k}`], title: `${lab}: ${k === "filled" ? "executed fill" : k === "submitted" ? "sent to a broker, not yet filled" : "proposed by a strategy, never sent"}` }, el("span", { class: "mk-glyph", "aria-hidden": "true" }, g), lab)),
      markers.length ? el("span", { class: "muted" }, `${markers.length} from real trade records`) : el("span", { class: "muted", "data-testid": "markers-empty", title: markersReason || "" }, "none: no real trade records exist"),
      ...(sealedFrom() ? [el("span", { class: "mk-lg mk-lg-sealed", title: meta.sealed_final_test.note }, el("span", { class: "mk-swatch", "aria-hidden": "true" }), "Sealed final-test period")] : []),
      ...(last?.partial ? [el("span", { class: "mk-lg mk-lg-partial" }, el("span", { class: "mk-glyph", "aria-hidden": "true" }, "◦"), "Partial bar (period in progress)")] : []));
    mstrip.replaceChildren(...tm.slice(0, 20).map((x) => el("button", { type: "button", class: "mk-mbtn", dataset: { markerId: x.id }, on: { click: () => openEvidence([x._m]) } },
      `${x._m.t ?? x.time} · ${STATE_META[x._m.state] ? STATE_META[x._m.state][1] : "Marker"} · ${x._m.side || ""}`)));
  }
  function drawChips() {
    const m = meta, asOf = quote?.as_of || meta.last_session || bars.at(-1)?.t, fr = quoteFreshness(quote);
    const mk = (kind, label, why, ic) => { const c = ui.badge(kind, label, ic); if (why) c.title = why; return c; };
    const all = [
      mk("info", "IBKR connector (daily files)", `Source: ${m.source}. Retrieved ${m.retrieved_on}.`, "data"),
      mk(fr.kind, `${fr.label}${asOf ? " · as of " + asOf : ""}`, fr.why, fr.icon),
      mk("neutral", `Feed ${m.feed}${m.delayed_seconds ? " " + Math.round(m.delayed_seconds / 60) + " min" : ""}`, "Connector data is delayed; these are completed daily bars, not a live feed.", "clock"),
      mk("neutral", "America/New_York", "Bar dates are New York session dates.", "clock"),
      mk(m.adjusted_for_dividends ? "ok" : "warn", m.adjusted_for_dividends ? "Dividend-adjusted" : "Unadjusted for dividends", "Prices are as traded; dividends/splits are not applied.", "info"),
      mk("neutral", "Regular session", "Extended hours: unavailable.", "info"),
    ];
    if (sealedFrom()) all.splice(2, 0, mk("warn", `Sealed final-test period from ${sealedFrom()}`, m.sealed_final_test.note, "lock"));
    if (bars.at(-1)?.partial) all.push(mk("warn", "Last bar partial", "The latest weekly/monthly bar covers a period still in progress.", "alert"));
    if (compact) {
      const [a, b, ...rest0] = all, rest = rest0.slice(1), sealedChip = rest0[0]?.textContent.startsWith("Sealed") ? rest0[0] : null;
      const more = ui.btn("Data details", { icon: "info", kind: "ghost", onClick: (e) => ui.popover(e.currentTarget, (box) => box.append(el("div", { class: "pop-title" }, "Data provenance"), kv([["Source", m.source], ["Instrument", `${m.symbol} · ${m.exchange} · conId ${m.conid}`], ["Currency", m.currency], ["Feed", `${m.feed}${m.delayed_seconds ? ` (${m.delayed_seconds}s)` : ""}`], ["Retrieved", m.retrieved_on], ["As of", asOf], ["Timezone", m.timezone], ["Adjustment", m.adjusted_for_dividends ? "dividend-adjusted" : "unadjusted for dividends"], ["Session", "regular only; extended hours unavailable"], ["Bars", `${m.n} (${interval})`]]) ), { label: "Data provenance", width: 360 }) });
      chips.replaceChildren(...[a, b, sealedChip].filter(Boolean), ...rest.filter((x) => x.textContent.startsWith("Last bar")), more);
    } else chips.replaceChildren(...all);
  }

  // ---------------------------------------------------------------- range / nav / readout
  function applyRange() {
    if (!chart || !bars.length) return;
    const days = RANGES.find((r) => r[0] === range)?.[1];
    if (!days) { chart.timeScale().fitContent(); return; }
    const lastMs = Date.parse(bars.at(-1).t + "T00:00:00Z"), from = new Date(lastMs - days * 86400000).toISOString().slice(0, 10);
    let i = bars.findIndex((b) => b.t >= from); if (i < 0) i = 0;
    chart.timeScale().setVisibleLogicalRange({ from: i - 0.5, to: bars.length - 1 + 3 });
  }
  function pan(dir) { const r = chart?.timeScale().getVisibleLogicalRange(); if (!r) return; const d = (r.to - r.from) * 0.2 * dir; chart.timeScale().setVisibleLogicalRange({ from: r.from + d, to: r.to + d }); }
  function zoom(f) { const r = chart?.timeScale().getVisibleLogicalRange(); if (!r) return; const c = (r.to + r.from) / 2, h = ((r.to - r.from) / 2) * f; chart.timeScale().setVisibleLogicalRange({ from: c - h, to: c + h }); }
  canvas.addEventListener("keydown", (e) => {
    const k = e.key; if (!chart) return;
    if (k === "ArrowLeft") pan(-1); else if (k === "ArrowRight") pan(1); else if (k === "+" || k === "=" || k === "ArrowUp") zoom(0.8); else if (k === "-" || k === "_" || k === "ArrowDown") zoom(1.25);
    else if (k === "Home") chart.timeScale().fitContent(); else if (k === "End") chart.timeScale().scrollToRealTime(); else return;
    e.preventDefault();
  });
  function showReadout(i) {
    const hovering = i != null, b = bars[hovering ? i : bars.length - 1]; if (!b) return readout.replaceChildren();
    const prev = bars[(hovering ? i : bars.length - 1) - 1], chg = prev ? ((b.close - prev.close) / prev.close) * 100 : null;
    const cur = meta?.currency || "USD", item = (k, v, cls) => el("span", { class: "mk-ro" }, el("span", { class: "muted" }, k), el("b", { class: cls || "" }, v));
    readout.dataset.hover = hovering ? "1" : "0";
    readout.replaceChildren(el("span", { class: "mk-ro-date" }, `${b.t}${hovering ? "" : " (latest)"}`), item("O", fmt.price(b.open)), item("H", fmt.price(b.high)), item("L", fmt.price(b.low)), item("C", fmt.price(b.close)),
      item("Vol", fmt.compact(b.volume)), item("Chg", chg == null ? "—" : fmt.pct(chg), fmt.dir(chg)), el("span", { class: "mk-ro-note muted" }, `${cur}, ${interval}${b.partial ? ", partial" : ""}`), inSealed(b.t) ? el("span", { class: "mk-ro-sealed", title: meta.sealed_final_test.note }, "sealed final-test period") : null);
  }
  function onCross(p) { if (!bars.length) return; if (!p?.time || !byTime.has(p.time)) return showReadout(null); showReadout(byTime.get(p.time)); }
  function onClick(p) {
    if (!p?.time || !markers.length) return;
    const hit = tradeMarkers().filter((x) => x.time === p.time).map((x) => x._m); if (!hit.length) return;
    const b = bars[byTime.get(p.time)], y1 = candles.priceToCoordinate(b.high), y2 = candles.priceToCoordinate(b.low);
    if (p.point && y1 != null && y2 != null && (p.point.y < y1 - 34 || p.point.y > y2 + 34)) return;
    openEvidence(hit);
  }

  // ---------------------------------------------------------------- evidence drawer
  function openEvidence(list) {
    ui.drawer({ title: list.length > 1 ? `${list.length} markers on ${list[0].t || ""}` : "Marker evidence", width: 460, label: "Marker evidence", content: (body) => {
      for (const m of list) {
        const st = STATE_META[m.state] ? m.state : "proposed";
        body.append(el("section", { class: "mk-evidence", dataset: { markerId: m.id } },
          el("div", { class: "row" }, ui.badge(st === "filled" ? "ok" : st === "submitted" ? "warn" : "info", STATE_META[st][1]), m.synthetic ? ui.badge("demo", "SYNTHETIC fixture (software test only)", "flask") : null),
          kv([["Instrument", `${ins.symbol} · ${ins.exchange} · conId ${ins.conid}`], ["Side", safeText(m.side) || "—"], ["Time", safeText(m.t ?? m.time)], ["Quantity", m.qty == null ? "—" : fmt.qty(m.qty)],
            ["Price", m.price == null ? "—" : `${fmt.price(m.price)} ${meta?.currency || ""}`], ["Strategy", safeText(m.strategy) || "manual / none"], ["Order id", safeText(m.order_id) || "—"], ["Trade id", safeText(m.trade_id) || "—"], ["Source", safeText(m.source) || "—"]]),
          m.evidence && typeof m.evidence === "object" ? el("div", {}, el("h3", { class: "mk-h3" }, "Evidence"), kv(Object.entries(m.evidence).slice(0, 20).map(([k, v]) => [safeText(k, 60), safeText(typeof v === "object" ? JSON.stringify(v) : v, 300)]))) : el("p", { class: "muted" }, "No evidence attached to this marker."),
          el("p", { class: "muted mk-fine" }, "Markers are only drawn from real trade records. A proposal is not an order; a submission is not a fill.")));
      }
    } });
  }

  // ---------------------------------------------------------------- public
  async function setInstrument(i) {
    ins = i; L = L || await loadChartLib().catch((e) => { overlayMsg.hidden = false; overlayMsg.replaceChildren(ui.empty("Chart library unavailable", e.message)); throw e; });
    title.replaceChildren(el("span", { class: "mk-sym" }, i.symbol), el("span", { class: "mk-ex muted" }, `${i.exchange} · conId ${i.conid}`));
    text(sub, i.name || ""); if (!i.name) sub.replaceChildren(fmt.missing("Company name unavailable: no entitled source connected"));
    drawControls(); await fetchBars();
  }
  drawControls();
  return { el: root, setInstrument, refresh: fetchBars, chartHandle: () => canvas.__market, destroy() { destroyed = true; ro?.disconnect(); mo?.disconnect(); try { chart?.remove(); } catch { /* already removed */ } chart = null; cleanups.forEach((f) => f()); } };
}
