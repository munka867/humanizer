// Equity / drawdown charts (TradingView Lightweight Charts 5.2.1, Apache-2.0, vendored) and an SVG quantile-range plot.
import { el, svgEl } from "../core/dom.js";
import { loadScript } from "./kit.js";

const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
export async function lib() { await loadScript("/static/vendor/lightweight-charts.standalone.production.js"); return window.LightweightCharts; }

function baseOpts(height) {
  return { height, autoSize: true, layout: { background: { color: css("--surface") }, textColor: css("--text-2"), attributionLogo: true, fontFamily: css("--font-sans") },
    grid: { vertLines: { color: css("--border") }, horzLines: { color: css("--border") } }, rightPriceScale: { borderColor: css("--border") },
    timeScale: { borderColor: css("--border"), timeVisible: false }, crosshair: { mode: 1 } };
}
/** series rows: [date, net, gross, costs, n, equity, dd]. Returns {el, destroy}. */
function autoFit(host, chart) {
  let done = false;
  const ro = new ResizeObserver(() => { if (host.clientWidth > 0) { chart.timeScale().fitContent(); if (!done) { done = true; } } });
  ro.observe(host);
  return () => ro.disconnect();
}
export async function equityChart(rows, { height = 240 } = {}) {
  const LW = await lib(); const host = el("div", { class: "chart", style: { height: height + "px" }, role: "img", "aria-label": "Cumulative net and gross P&L by trade date" });
  const chart = LW.createChart(host, baseOpts(height));
  const net = chart.addSeries(LW.LineSeries, { color: css("--accent"), lineWidth: 2, title: "net", priceFormat: { type: "price", precision: 0, minMove: 1 } });
  const gross = chart.addSeries(LW.LineSeries, { color: css("--warn"), lineWidth: 1, lineStyle: 2, title: "gross", priceFormat: { type: "price", precision: 0, minMove: 1 } });
  let g = 0; const gd = rows.map((r) => ({ time: r[0], value: (g += r[2]) }));
  net.setData(rows.map((r) => ({ time: r[0], value: r[5] }))); gross.setData(gd); chart.timeScale().fitContent(); const stop = autoFit(host, chart);
  return { el: host, chart, destroy: () => { stop(); chart.remove(); } };
}
export async function drawdownChart(rows, { height = 160 } = {}) {
  const LW = await lib(); const host = el("div", { class: "chart", style: { height: height + "px" }, role: "img", "aria-label": "Drawdown of cumulative net P&L" });
  const chart = LW.createChart(host, baseOpts(height));
  const s = chart.addSeries(LW.AreaSeries, { lineColor: css("--neg"), topColor: "transparent", bottomColor: css("--bad-bg") || css("--neg"), lineWidth: 1, title: "drawdown", priceFormat: { type: "price", precision: 0, minMove: 1 } });
  s.setData(rows.map((r) => ({ time: r[0], value: r[6] }))); chart.timeScale().fitContent(); const stop = autoFit(host, chart);
  return { el: host, chart, destroy: () => { stop(); chart.remove(); } };
}

/** Quantile-range plot: for each stat a bar p05..p95, box p25..p75, tick p50, mean dot. Axis per row (own scale). */
export function quantilePlot(stats, fmtv) {
  const W = 520, rowH = 54, padL = 150, padR = 24, H = rowH * stats.length + 8; const root = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "qplot", role: "img", "aria-label": "Simulated outcome quantile ranges" });
  stats.forEach((s, i) => {
    const y = i * rowH + 26; const lo = Math.min(s.p05, 0), hi = Math.max(s.p95, 0, s.mean); const span = hi - lo || 1; const x = (v) => padL + ((v - lo) / span) * (W - padL - padR);
    root.append(svgEl("text", { x: 4, y: y + 4, class: "q-label" }, s.label));
    root.append(svgEl("line", { x1: x(s.p05), x2: x(s.p95), y1: y, y2: y, class: "q-whisk" }));
    root.append(svgEl("rect", { x: x(s.p25), y: y - 9, width: Math.max(1, x(s.p75) - x(s.p25)), height: 18, class: "q-box", rx: 3 }));
    root.append(svgEl("line", { x1: x(s.p50), x2: x(s.p50), y1: y - 11, y2: y + 11, class: "q-med" }));
    root.append(svgEl("circle", { cx: x(s.mean), cy: y, r: 3, class: "q-mean" }));
    if (lo < 0 && hi > 0) root.append(svgEl("line", { x1: x(0), x2: x(0), y1: y - 14, y2: y + 14, class: "q-zero" }));
    root.append(svgEl("text", { x: x(s.p05), y: y + 24, class: "q-tick", "text-anchor": "start" }, fmtv(s.p05)));
    root.append(svgEl("text", { x: x(s.p95), y: y + 24, class: "q-tick", "text-anchor": "end" }, fmtv(s.p95)));
  });
  return root;
}
