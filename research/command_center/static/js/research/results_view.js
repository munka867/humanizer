// Result rendering shared by Backtesting > Results / Monte Carlo. External text only via el()/text nodes.
import { el } from "../core/dom.js";
import { icon } from "../core/icons.js";
import { card, defLabel, kv, note, verdictBadge, errText } from "./kit.js";
import { equityChart, drawdownChart, quantilePlot } from "./charts.js";

let DEFS = {};
const sum = (rows, i) => rows.reduce((a, r) => a + r[i], 0);
const base = (id) => (id.startsWith("committed:") ? `/api/runs/committed/${id}` : `/api/experiments/${id}`);
export const runPath = base;

function kpi(ctx, label, def, value, sub, cls) {
  return el("div", { class: "kpi" }, el("div", { class: "kpi-l" }, defLabel(ctx, label, def)), el("div", { class: ["kpi-v", cls] }, value), sub ? el("div", { class: "kpi-s" }, sub) : null);
}
const dirCls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");

export function sealedPanels(ctx, res) {
  const oos = res.evidence.out_of_sample_test, fp = res.evidence.forward_paper;
  return [
    el("section", { class: "seg-panel sealed", "aria-label": "Out-of-sample (sealed final test)" }, el("header", {}, el("h3", {}, "Out-of-sample (final test)"), ctx.ui.badge("neutral", "sealed", "lock")),
      el("p", { class: "muted" }, oos.reason), el("p", { class: "hint" }, "No number is shown here and none can be requested from this screen.")),
    el("section", { class: "seg-panel sealed", "aria-label": "Forward paper results" }, el("header", {}, el("h3", {}, "Forward paper"), ctx.ui.badge("neutral", "not available", "lock")),
      el("p", { class: "muted" }, fp.reason)),
  ];
}

export function verdictPanel(ctx, res) {
  const v = res.verdict;
  if (!v) return card("Development verdict", el("p", { class: "muted" }, "No verdict for this run. The pipeline computes a development verdict (train + validation) only when it runs the validation segment."));
  return card("Development verdict (as produced by the pipeline)", el("div", { class: "row" }, verdictBadge(ctx, v.value), el("span", { class: "muted" }, v.rule)),
    el("div", {}, el("strong", {}, "Reasons / failed gates, verbatim:"), el("ul", { class: "list-plain reasons" }, v.reasons.map((r) => el("li", {}, icon(r.startsWith("not met") ? "x" : "info", 12), " ", r)))),
    el("p", { class: "hint" }, v.scope));
}

export function baselinesPanel(ctx, blk) {
  const d0 = blk.baselines.B_D0 || {}, d1 = blk.baselines.B_D1;
  const rows0 = Object.entries(d0).map(([k, s]) => ({ scope: k === "pooled" ? "pooled" : k, n: s.n, ev: s.ev_usd, bps: s.ev_bps }));
  const t0 = ctx.ui.table({ id: "bt.b0", ariaLabel: "Baseline B_D0", caption: "B_D0: every eligible day long, open to close", rows: rows0, rowKey: (r) => r.scope, exportable: false, columnMenu: false,
    columns: [{ key: "scope", label: "Scope", width: 100 }, { key: "n", label: "Days", align: "num", width: 80, format: "qty" }, { key: "ev", label: "EV $/day", align: "num", width: 100, format: "money" }, { key: "bps", label: "EV bps", align: "num", width: 90, format: "num" }] });
  const out = [el("h4", {}, defLabel(ctx, "B_D0 buy & hold open→close", DEFS.B_D0)), t0.el];
  if (d1) {
    const rows1 = ["usd", "bps", "r"].map((k) => ({ k: { usd: "EV $", bps: "EV bps", r: "EV R" }[k], ...d1[k] }));
    out.push(el("h4", {}, defLabel(ctx, "B_D1 matched random days", DEFS.B_D1)),
      ctx.ui.table({ id: "bt.b1", ariaLabel: "Baseline B_D1", caption: "B_D1: matched random-day permutation", rows: rows1, rowKey: (r) => r.k, exportable: false, columnMenu: false,
        columns: [{ key: "k", label: "Metric", width: 90 }, ...[["observed", "Observed"], ["null_mean", "Null mean"], ["null_p05", "Null p05"], ["null_p95", "Null p95"], ["p_one_sided", "p (1-sided)"], ["percentile", "Percentile"]].map(([key, label]) => ({ key, label, align: "num", width: 100, format: (v) => ctx.fmt.num(v, 3) }))] }).el);
  } else out.push(el("p", { class: "muted" }, "B_D1 not computed: no trades in this segment."));
  return out;
}

export async function segmentPanel(ctx, res, key, id, S) {
  const blk = res.evidence[key]; const title = key === "training" ? "Training" : "Validation";
  const p = blk.metrics.pooled; const defs = res.metric_definitions; DEFS = defs;
  const panel = el("section", { class: "seg-panel", "aria-label": `${title} results` });
  panel.append(el("header", {}, el("h3", {}, `${title} segment`), ctx.ui.badge("info", "in-sample for design decisions", "info"),
    el("span", { class: "muted" }, blk.window_utc ? `${String(blk.window_utc[0]).slice(0, 10)} → ${String(blk.window_utc[1]).slice(0, 10)} (purge ${String(blk.purge).replace(" 00:00:00", "")})` : "")));
  if (!p.n) { panel.append(note("warn", "No trades in this segment for the selected symbols, so there are no metrics.")); return panel; }
  const sel = blk.cost_stress.selected_scenario;
  const sers = el("div", {}, ctx.ui.skeleton({ lines: 3 })); panel.append(sers);
  let series = null;
  try { series = await ctx.api.get(runPath(id) + "/series"); } catch (e) { series = null; sers.replaceChildren(note("warn", `Series not available: ${errText(e)}`)); }
  const g = series ? sum(series.rows, 2) : null, c = series ? sum(series.rows, 3) : null;
  const k = el("div", { class: "kpis" },
    kpi(ctx, "Net P&L", "Sum of net P&L after modelled costs over the segment (USD)", ctx.fmt.money(p.total_usd), "after costs, x1", dirCls(p.total_usd)),
    kpi(ctx, "Gross P&L", "Sum of P&L before modelled costs (USD)", g == null ? ctx.fmt.missing("series not available") : ctx.fmt.money(g), "before costs", dirCls(g)),
    kpi(ctx, "Costs", defs.cost_stress, c == null ? ctx.fmt.missing("series not available") : ctx.fmt.money(c), "modelled, unverified assumptions"),
    kpi(ctx, "Expectancy", defs.ev_usd, ctx.fmt.money(p.ev_usd), `${ctx.fmt.num(p.ev_r, 3)} R · ${ctx.fmt.num(p.ev_bps, 1)} bps`, dirCls(p.ev_usd)),
    kpi(ctx, "95% CI of EV", defs.ci_usd, p.ci_usd ? `${ctx.fmt.money(p.ci_usd[0], { dp: 1 })} … ${ctx.fmt.money(p.ci_usd[1], { dp: 1 })}` : ctx.fmt.missing("not computed"), p.ci_usd && p.ci_usd[0] < 0 && p.ci_usd[1] > 0 ? "straddles zero" : null),
    kpi(ctx, "Trades", defs.n, ctx.fmt.qty(p.n), `${ctx.fmt.qty(p.n_days_with_trades)} distinct days`),
    kpi(ctx, "Exposure", defs.exposure_days, ctx.fmt.pct(p.exposure_days, { ratio: true, sign: false, dp: 1 }), "of candidate days"),
    kpi(ctx, "Hit rate", defs.hit, ctx.fmt.pct(p.hit, { ratio: true, sign: false, dp: 1 }), `PF ${ctx.fmt.num(p.pf, 2)}`),
    kpi(ctx, "Max drawdown", defs.maxdd_usd, ctx.fmt.money(p.maxdd_usd), "closed-trade basis"),
    sel && sel.row ? kpi(ctx, `EV at cost x${sel.multiplier}`, "Expectancy with costs re-priced at the selected cost multiplier", ctx.fmt.money(sel.row.ev_usd), `CI ${ctx.fmt.money(sel.row.ci_lo, { dp: 1 })} … ${ctx.fmt.money(sel.row.ci_hi, { dp: 1 })}`, dirCls(sel.row.ev_usd)) : null);
  panel.append(k);
  if (series && series.rows.length) {
    const eq = await equityChart(series.rows), dd = await drawdownChart(series.rows);
    sers.replaceChildren(el("h4", {}, "Cumulative P&L (net solid, gross dashed)"), eq.el, el("h4", {}, "Drawdown"), dd.el,
      el("p", { class: "hint" }, series.label + (series.origin ? ` · ${series.origin}` : "") + (series.reproduction_equals_committed_summary === true ? " · reproduction verified equal to the committed summary" : "")));
    S.charts.push(eq, dd);
  } else if (series) sers.replaceChildren(note("warn", "The series is empty."));
  // cost stress / per symbol / long-short / quarters
  const cs = ctx.ui.table({ id: "bt.cost", ariaLabel: "Cost stress", caption: "Cost stress (pooled)", rows: blk.cost_stress.rows, rowKey: (r) => r.cost_mult, exportable: false, columnMenu: false,
    columns: [{ key: "cost_mult", label: "Cost ×", align: "num", width: 80, format: (v) => ctx.fmt.num(v, 1) }, { key: "n", label: "n", align: "num", width: 70, format: "qty" }, { key: "ev_usd", label: "EV $", align: "num", width: 90, format: "money", colour: true },
      { key: "ci_lo", label: "CI low", align: "num", width: 90, format: "money" }, { key: "ci_hi", label: "CI high", align: "num", width: 90, format: "money" }, { key: "total_usd", label: "Total $", align: "num", width: 100, format: "money" }] });
  const ps = ctx.ui.table({ id: "bt.persym", ariaLabel: "Per symbol", caption: "Per symbol", rows: Object.entries(blk.metrics.per_symbol).map(([s, m]) => ({ s, ...m })), rowKey: (r) => r.s, exportable: false, columnMenu: false,
    emptyTitle: "No symbols", columns: [{ key: "s", label: "Symbol", width: 80 }, { key: "n", label: "n", align: "num", width: 60, format: "qty" }, { key: "ev_usd", label: "EV $", align: "num", width: 90, format: "money", missing: "no trades" },
      { key: "hit", label: "Hit", align: "num", width: 70, format: "pct", formatOpts: { ratio: true, sign: false, dp: 0 }, missing: "no trades" }, { key: "total_usd", label: "Total $", align: "num", width: 100, format: "money", missing: "no trades" }] });
  const ls = ctx.ui.table({ id: "bt.ls", ariaLabel: "Long vs short", caption: "Long vs short", rows: Object.entries(blk.metrics.long_short).map(([s, m]) => ({ s, ...m })), rowKey: (r) => r.s, exportable: false, columnMenu: false,
    columns: [{ key: "s", label: "Side", width: 70 }, { key: "n", label: "n", align: "num", width: 60, format: "qty" }, { key: "ev_usd", label: "EV $", align: "num", width: 90, format: "money" }, { key: "hit", label: "Hit", align: "num", width: 70, format: "pct", formatOpts: { ratio: true, sign: false, dp: 0 } }] });
  const qt = ctx.ui.table({ id: "bt.q", ariaLabel: "Per quarter", caption: "Per quarter (concentration)", rows: blk.quarters || [], rowKey: (r) => r.period, exportable: false, columnMenu: false,
    columns: [{ key: "period", label: "Quarter", width: 90 }, { key: "n", label: "n", align: "num", width: 60, format: "qty" }, { key: "pnl_net_sum", label: "Net $", align: "num", width: 100, format: "money", colour: true }, { key: "hit_rate", label: "Hit", align: "num", width: 70, format: "pct", formatOpts: { ratio: true, sign: false, dp: 0 } }] });
  panel.append(el("div", { class: "rs-grid-2" }, el("div", {}, el("h4", {}, "Cost stress"), cs.el), el("div", {}, el("h4", {}, "Per symbol"), ps.el), el("div", {}, el("h4", {}, "Long vs short"), ls.el), el("div", {}, el("h4", {}, "Per quarter"), qt.el)));
  panel.append(el("div", { class: "rs-grid-2" }, el("div", {}, ...baselinesPanel(ctx, blk)),
    el("div", {}, el("h4", {}, "Walk-forward & parameter sensitivity"),
      note("info", el("strong", {}, "Walk-forward windows: not computed. "), "The pre-registered design is one chronological train / validation / sealed-test split; there is no rolling re-fit."),
      el("div", { style: { height: "8px" } }),
      note("info", el("strong", {}, "Parameter sensitivity: not applicable. "), "H3 has no free parameters (DAILY_SPEC v1), so there is nothing to sweep."))));
  // trade log
  panel.append(await tradeLog(ctx, id));
  return panel;
}

async function tradeLog(ctx, id) {
  const box = el("div", { class: "rs-grid" }); const wrap = el("div", {}); const state = { off: 0, lim: 100, total: 0 };
  const info = el("span", { class: "muted" }); const prev = ctx.ui.btn("Previous", { icon: "chevron_left", onClick: () => go(-1) }); const next = ctx.ui.btn("Next", { icon: "chevron_right", onClick: () => go(1) });
  const t = ctx.ui.table({ id: "bt.trades", ariaLabel: "Trade log", caption: "Trade log (this page)", exportName: "trades-page", rows: [], rowKey: (r) => r.trade_id, pageSize: 100, compact: true,
    emptyTitle: "No trades loaded", emptyWhy: "The trade log comes from trades.csv written by the pipeline for this run.",
    columns: [{ key: "entry_date", label: "Entry", width: 100 }, { key: "symbol", label: "Symbol", width: 70 }, { key: "side", label: "Side", width: 60 },
      { key: "qty", label: "Qty", align: "num", width: 80, format: "qty", formatOpts: { dp: 2 } }, { key: "entry_px", label: "Entry px", align: "num", width: 90, format: "num" }, { key: "exit_px", label: "Exit px", align: "num", width: 90, format: "num" },
      { key: "pnl_gross", label: "Gross $", align: "num", width: 90, format: "money" }, { key: "costs", label: "Costs $", align: "num", width: 80, format: "money" }, { key: "pnl_net", label: "Net $", align: "num", width: 90, format: "money", colour: true },
      { key: "r_multiple", label: "R", align: "num", width: 70, format: "num", title: "Net P&L / (qty × ATR14); no stop exists" }] });
  async function go(d) { state.off = Math.max(0, state.off + d * state.lim); await load(); }
  async function load() {
    try {
      const r = await ctx.api.get(`${runPath(id)}/trades?offset=${state.off}&limit=${state.lim}`);
      state.total = r.total; t.setRows(r.trades); info.textContent = `${r.total ? state.off + 1 : 0}–${Math.min(state.off + state.lim, r.total)} of ${r.total} trades`;
      prev.disabled = state.off === 0; next.disabled = state.off + state.lim >= r.total;
    } catch (e) { wrap.replaceChildren(note("warn", `Trade log not available: ${errText(e)}`)); }
  }
  wrap.append(el("h4", {}, "Trade log"), el("div", { class: "pager" }, prev, info, next), t.el);
  await load();
  box.replaceChildren(wrap); box.style.gridTemplateColumns = "1fr";
  return box;
}

export function runHeader(ctx, res) {
  const r = res.run;
  return card(null, el("div", { class: "row" }, el("strong", {}, res.strategy.pipeline_strategy_id || res.strategy.id), ...res.labels.map((l) => ctx.ui.badge(l.includes("SYNTHETIC") ? "demo" : "neutral", l, l.includes("SYNTHETIC") ? "flask" : "info")),
    ctx.ui.badge("neutral", "no profitability or readiness claim", "shield")),
    kv([["Run", el("code", {}, res.id)], ["Segment", r.segment], ["Symbols", (r.symbols || []).join(", ")], ["Seed", r.seed == null ? ctx.fmt.missing("not recorded") : String(r.seed)], ["Cost scenario headline", r.cost_scenario || "x1 (committed default)"],
      r.notes ? ["Notes", r.notes] : null, r.deviates_from_preregistered_defaults ? ["Deviation", "differs from the pre-registered symbols/seed; record it in docs/EXPERIMENT_LOG.md before acting on it"] : null]),
    el("details", {}, el("summary", {}, "Caveats"), el("ul", { class: "list-plain" }, res.caveats.map((c) => el("li", {}, c)))));
}

export function mcPanel(ctx, res) {
  const out = [];
  for (const key of ["training", "validation"]) {
    const blk = res.evidence[key]; if (!blk) continue; const mc = blk.monte_carlo;
    if (!mc) { out.push(card(`Monte Carlo — ${key}`, note("info", "No Monte Carlo block: the segment had no trades."))); continue; }
    const P = mc.percentiles; const lab = { ev_per_trade_usd: ["EV per trade ($)", (v) => ctx.fmt.money(v, { dp: 1 })], total_pnl_usd: ["Total P&L ($)", (v) => ctx.fmt.money(v, { dp: 0 })], max_drawdown_usd: ["Max drawdown ($)", (v) => ctx.fmt.money(v, { dp: 0 })], longest_losing_streak: ["Longest losing streak (trades)", (v) => String(Math.round(v))] };
    const stats = Object.entries(lab).filter(([k]) => P[k]).map(([k, [label]]) => ({ label, ...P[k] }));
    const fmtFor = (s) => lab[Object.keys(lab).find((k) => lab[k][0] === s.label)][1];
    const plotBlocks = stats.map((s) => quantilePlot([s], fmtFor(s)));
    const rows = Object.entries(P).map(([k, v]) => ({ stat: lab[k] ? lab[k][0] : k, ...v }));
    const t = ctx.ui.table({ id: "bt.mc." + key, ariaLabel: "Simulated percentiles", caption: "Simulated outcome percentiles", rows, rowKey: (r) => r.stat, exportName: "monte-carlo", columnMenu: false,
      columns: [{ key: "stat", label: "Statistic", width: 220 }, ...["mean", "sd", "p05", "p25", "p50", "p75", "p95"].map((k) => ({ key: k, label: k, align: "num", width: 90, format: (v) => ctx.fmt.num(v, 1) }))] });
    const pr = mc.probabilities || {};
    const PL = { p_ev_le_0: "P(EV per trade ≤ 0)", p_total_pnl_lt_0: "P(total P&L < 0)", p_ruin: "P(ruin) as defined by tradelab montecarlo", p_breach_max_dd_limit: "P(breach max-drawdown limit)", p_breach_daily_loss_limit: "P(breach daily-loss limit)" };
    const probs = Object.entries(pr).map(([k, v]) => [PL[k] || k, v == null ? ctx.fmt.missing("threshold not configured in the pipeline run") : ctx.fmt.pct(v, { ratio: true, sign: false, dp: 1 })]);
    out.push(card(`Monte Carlo — ${key}`,
      note("warn", el("strong", {}, "Conditional simulation — resamples the same evidence, not a probability the strategy works. "), "It resamples this segment's observed trades and adds no independent market evidence."),
      kv([["Method", `${mc.method} (resamples whole trading days)`], ["Seed", String(mc.seed)], ["Paths", ctx.fmt.qty(mc.paths)], ["Horizon", mc.horizon.definition],
        ["Trades per path", mc.horizon.trades_per_path ? `mean ${ctx.fmt.num(mc.horizon.trades_per_path.mean, 1)} (p05 ${mc.horizon.trades_per_path.p05} … p95 ${mc.horizon.trades_per_path.p95})` : "—"],
        ["Thresholds", `account size ${ctx.fmt.money(mc.thresholds.account_size_usd, { dp: 0 })}; max-drawdown limit: ${mc.thresholds.max_drawdown_limit_usd ?? "not set"}; daily-loss limit: ${mc.thresholds.daily_loss_limit_usd ?? "not set"}`], ["Threshold note", mc.thresholds.note]]),
      el("h4", {}, "Outcome ranges (whisker p05–p95, box p25–p75, line median, dot mean)"),
      el("p", { class: "hint" }, "The pipeline stores percentiles, not the full simulated distribution, so these are quantile-range plots rather than histograms."),
      el("div", { class: "rs-grid-2" }, ...plotBlocks), t.el,
      el("h4", {}, "Breach and loss probabilities (simulated, conditional)"), kv(probs), el("p", { class: "hint" }, mc.pipeline_caveat)));
  }
  return out;
}
