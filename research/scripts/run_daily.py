#!/usr/bin/env python
"""Daily H3 study (DAILY_SPEC v1). Usage:
    python scripts/run_daily.py --segment train
    python scripts/run_daily.py --segment validation          # also computes train for the verdict inputs
    python scripts/run_daily.py --segment test --variant D3_V1_c2_both   # REFUSED unless a freeze record exists
    python scripts/run_daily.py --freeze D3_V1_c2_both         # coordinator only: records the freeze, reads no data
Outputs: results/daily/<segment>_summary.json and results/daily/<segment>_report.md (aggregates only, no raw bars).
Optional (command-centre experiment builder; defaults leave the committed behaviour unchanged):
    --out-dir DIR   write outputs there instead of results/daily      --data-dir DIR   read {SYMBOL}_1d.csv from DIR
    --symbols A,B   restrict the universe (subset of the configured one)   --seed N   override all three seeds
    --progress      emit machine-readable '##PROGRESS {json}' lines on stderr after each finished step."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from tradelab.daily import baselines as B  # noqa: E402
from tradelab.daily import engine as E  # noqa: E402
from tradelab.daily import report as R  # noqa: E402
from tradelab.validation import final_test_guard as G  # noqa: E402
from tradelab.validation.splits import apply_split, classify_trades, make_split  # noqa: E402
from tradelab.validation.verdict import classify_development  # noqa: E402

OUT = ROOT / "results" / "daily"
_P = {"on": False, "done": 0, "total": 0}


def _plan(total: int) -> None:
    _P["done"], _P["total"] = 0, total


def _step(name: str) -> None:
    """Progress is only reported after a step has really finished (never estimated)."""
    _P["done"] += 1
    if _P["on"]:
        print("##PROGRESS " + json.dumps({"step": name, "done": _P["done"], "total": _P["total"]}), file=sys.stderr, flush=True)


def load_cfg():
    cfg = yaml.safe_load((ROOT / "configs" / "daily_strategy.yaml").read_text())
    costs_raw = yaml.safe_load((ROOT / "configs" / "daily_costs.yaml").read_text())
    return cfg, costs_raw


def config_hash_for(variant: str, cfg: dict, costs_raw: dict) -> str:
    return G.config_hash({"variant": variant, "strategy": cfg, "costs": costs_raw})


def sid(cfg, variant):
    return E.strategy_id(variant, cfg["universe"])


def guard_test_segment(variant: str, cfg: dict, costs_raw: dict, evaluator, log_path=G.DEFAULT_LOG):
    """The ONLY route to the test segment. Raises FinalTestRefused (and logs the refused attempt) unless frozen."""
    return G.evaluate_final_test(sid(cfg, variant), config_hash_for(variant, cfg, costs_raw), evaluator,
                                 log_path=log_path, purpose="daily H3 single sealed test look")


def build(cfg, costs_raw):
    costs, scen = E.load_costs(ROOT / "configs" / "daily_costs.yaml")
    bars = {s: R.load_bars(s, ROOT / cfg["data_dir"]) for s in cfg["universe"]}
    first = min(b.index[0] for b in bars.values())
    last = max(b.index[-1] for b in bars.values())
    split = make_split(first, last, tuple(cfg["split"]["fractions"]), pd.Timedelta(days=cfg["split"]["purge_days"]))
    return costs, bars, split


def seg_candidates(bars, cfg, split, seg):
    """Eligible t+1 opportunities per symbol restricted to the segment (by entry time, purged/straddle rule)."""
    out = {}
    for s, b in bars.items():
        c = E.candidates(b, s, cfg["notional_usd"], cfg["atr_n"], cfg["max_gap_calendar_days"])
        lab = classify_trades(E.make_trades(c, +1, "x", E.DailyCosts()), split)
        out[s] = c[(lab == seg).to_numpy()].reset_index(drop=True)
    return out


def analyse(seg, cfg, costs, bars, split, variants, progress=False):
    nv = cfg["n_variants_registered_overall"]
    seeds = cfg["seeds"]
    cands = seg_candidates(bars, cfg, split, seg)
    all_dates = pd.concat([c["entry_ts"] for c in cands.values()]).dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    n_days = int(all_dates.nunique())
    res = {"segment": seg, "n_candidate_days_union": n_days,
           "n_eligible_per_symbol": {s: len(c) for s, c in cands.items()}, "variants": {}}
    # B_D0
    d0 = {s: E.make_trades(c, +1, "B_D0", costs) for s, c in cands.items()}
    d0_all = pd.concat(d0.values(), ignore_index=True)
    res["B_D0"] = {"pooled": {"n": len(d0_all), "ev_usd": float(d0_all["pnl_net"].mean()), "ev_bps": float(R.bps_of(d0_all).mean()),
                              "ev_r": float(d0_all["r_multiple"].mean())},
                   **{s: {"n": len(t), "ev_usd": float(t["pnl_net"].mean()), "ev_bps": float(R.bps_of(t).mean()),
                          "ev_r": float(t["r_multiple"].mean())} for s, t in d0.items()}}
    if progress:
        _step(f"{seg}: baseline B_D0")
    trades_by_variant = {}
    for v in variants:
        which = cfg["variants"][v]["sides"]
        full = E.run_variant(bars, v, which, costs, cfg["notional_usd"], cfg["atr_n"], cfg["max_gap_calendar_days"])
        sp = apply_split(full, split)
        t = sp.segment(seg).reset_index(drop=True)
        trades_by_variant[v] = t
        vr = {"strategy_id": sid(cfg, v), "pooled": R.summarize(t, n_days, seeds["bootstrap"], nv),
              "per_symbol": {s: R.summarize(t[t.symbol == s], n_days, seeds["bootstrap"], nv) for s in cfg["universe"]},
              "long_short": R.long_short(t), "cost_stress": R.cost_table(t, seeds["bootstrap"]),
              "quarters": R.quarter_table(t), "monte_carlo": R.monte_carlo(t, seeds["montecarlo"], cfg["mc"]["n_sims"], cfg["mc"]["method"], cfg["mc"]["account_size"]),
              "dropped_by_split": {k: v_ for k, v_ in sp.dropped.items() if k != "outside"}}
        counts = {s: (int(((t.symbol == s) & (t.side == 1)).sum()), int(((t.symbol == s) & (t.side == -1)).sum())) for s in cfg["universe"]}
        vr["signal_counts_per_symbol_long_short"] = counts
        if len(t):
            null = B.matched_permutation_null(cands, counts, costs, seeds["baseline"], cfg["b_d1_draws"])
            obs = {"usd": float(t["pnl_net"].mean()), "r": float(t["r_multiple"].mean()), "bps": float(R.bps_of(t).mean())}
            vr["B_D1"] = B.null_summary(null, obs)
        res["variants"][v] = vr
        if progress:
            _step(f"{seg}: {v} (summary, cost stress, Monte Carlo, B_D1)")
    return res, trades_by_variant


def verdict_inputs(cfg, costs, bars, split, v):
    """Train+validation numbers for validation.verdict.classify_development, pooled across symbols (by date)."""
    tr_res, tr_t = analyse("train", cfg, costs, bars, split, [v])
    va_res, va_t = analyse("validation", cfg, costs, bars, split, [v])
    trv, vav = tr_res["variants"][v], va_res["variants"][v]
    vt = va_t[v]
    cs = {r["cost_mult"]: r for r in vav["cost_stress"]}
    d1 = vav.get("B_D1")
    e = {"n_train": trv["pooled"]["n"], "n_val": vav["pooled"]["n"], "n_val_days": vav["pooled"].get("n_days_with_trades", 0),
         "ev_train_1x": trv["pooled"].get("ev_usd"), "ev_val_1x": vav["pooled"].get("ev_usd"),
         "ev_val_2x": cs.get(2.0, {}).get("ev_usd"), "val_ci_lo_at_pass_mult": cs.get(1.5, {}).get("ci_lo"),
         "val_ci_hi_1x": cs.get(1.0, {}).get("ci_hi"), "p_bonferroni_val": vav["pooled"].get("p_bonferroni_day"),
         "dsr": vav["pooled"].get("dsr"), "positive_quarter_fraction": R.positive_quarter_fraction(vt),
         "max_period_share": R.month_max_share(vt), "baseline_percentile": d1["usd"]["percentile"] if d1 else None}
    verdict, reasons = classify_development(e)
    return {"inputs": e, "verdict": verdict, "reasons": reasons}


# ------------------------------------------------------------------ markdown
def f(x, nd=2):
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:,.{nd}f}"


def ci(c, nd=2):
    return "n/a" if not c else f"[{f(c[0], nd)}, {f(c[1], nd)}]"


def md_report(res, verdict=None):
    L = [f"## Segment: {res['segment']}  (candidate trade-days in union: {res['n_candidate_days_union']}; eligible per symbol: {res['n_eligible_per_symbol']})", ""]
    L += ["### Performance (net of costs x1)", "",
          "| variant | scope | n | EV $/trade | EV R | EV bps | hit | avg win $ | avg loss $ | PF | maxDD $ | days in mkt | total $ | day-block 95% CI EV $ | 95% CI EV R |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for v, vr in res["variants"].items():
        for scope, s in [("pooled-by-date", vr["pooled"])] + list(vr["per_symbol"].items()):
            if s["n"] == 0:
                L.append(f"| {v} | {scope} | 0 | | | | | | | | | | | | |")
                continue
            L.append(f"| {v} | {scope} | {s['n']} | {f(s['ev_usd'])} | {f(s['ev_r'], 3)} | {f(s['ev_bps'])} | {f(s['hit'], 3)} | {f(s['avg_win'])} | {f(s['avg_loss'])} | {f(s['pf'], 2)} | {f(s['maxdd_usd'])} | {f(s['exposure_days'], 3)} | {f(s['total_usd'])} | {ci(s['ci_usd'])} | {ci(s['ci_r'], 3)} |")
    L += ["", "### Cost stress (pooled; EV $/trade with day-block 95% CI)", "", "| variant | cost x | n | EV $ | CI lo | CI hi | hit |", "|---|---|---|---|---|---|---|"]
    for v, vr in res["variants"].items():
        for r in vr["cost_stress"]:
            L.append(f"| {v} | {r['cost_mult']} | {r['n']} | {f(r['ev_usd'])} | {f(r['ci_lo'])} | {f(r['ci_hi'])} | {f(r['hit_rate'], 3)} |")
    d0 = res["B_D0"]
    L += ["", "### Baselines", "", "B_D0 = every eligible day long open->close (same sizing/costs):", "",
          "| scope | n days | EV $ | EV bps | EV R |", "|---|---|---|---|---|"]
    for k, s in d0.items():
        L.append(f"| {k} | {s['n']} | {f(s['ev_usd'])} | {f(s['ev_bps'])} | {f(s['ev_r'], 3)} |")
    L += ["", "B_D1 = matched random-day permutation (10,000 draws, per-instrument counts and long/short split matched). p = one-sided P(null >= observed), +1 smoothed.", "",
          "| variant | metric | observed | null mean | null p05 | null p95 | p (one-sided) | percentile |", "|---|---|---|---|---|---|---|---|"]
    for v, vr in res["variants"].items():
        if "B_D1" in vr:
            for k, nm in (("usd", "EV $"), ("bps", "EV bps"), ("r", "EV R")):
                d = vr["B_D1"][k]
                L.append(f"| {v} | {nm} | {f(d['observed'], 3)} | {f(d['null_mean'], 3)} | {f(d['null_p05'], 3)} | {f(d['null_p95'], 3)} | {f(d['p_one_sided'], 4)} | {f(d['percentile'], 3)} |")
    L += ["", "### Long vs short split (pooled)", "", "| variant | side | n | EV $ | EV bps | EV R | hit |", "|---|---|---|---|---|---|---|"]
    for v, vr in res["variants"].items():
        for side, s in vr["long_short"].items():
            L.append(f"| {v} | {side} | {s['n']} | {f(s['ev_usd'])} | {f(s['ev_bps'])} | {f(s['ev_r'], 3)} | {f(s['hit'], 3)} |")
    L += ["", "### Per-quarter (pooled, by NY entry date)", "", "| variant | quarter | n | net sum $ | mean $ | hit |", "|---|---|---|---|---|---|"]
    for v, vr in res["variants"].items():
        for q in vr["quarters"]:
            L.append(f"| {v} | {q['period']} | {q['n']} | {f(q['pnl_net_sum'])} | {f(q['pnl_net_mean'])} | {f(q['hit_rate'], 3)} |")
    L += ["", "### Concentration, inference, Monte Carlo (pooled)", "",
          "Top-5 share = (sum of 5 best trades)/(total net PnL); meaningless if total <= 0. p_day = day-clustered t-test; Bonferroni N=16; DSR assumptions in the notes.", "",
          "| variant | top-5 share of PnL | longest losing streak | p_day 1-sided | p_day 2-sided | Bonferroni p (N=16) | DSR |", "|---|---|---|---|---|---|---|"]
    for v, vr in res["variants"].items():
        s = vr["pooled"]
        if s["n"]:
            L.append(f"| {v} | {f(s['top5_share'], 2)} | {s['longest_losing_streak']} | {f(s['p_day_one_sided'], 3)} | {f(s['p_day_two_sided'], 3)} | {f(s['p_bonferroni_day'], 3)} | {f(s['dsr'], 3)} |")
    L += ["", "Monte Carlo (day_block, 5000 paths, seed 20261008). RESAMPLES THE SAME EVIDENCE; adds no independent market evidence.", "",
          "| variant | statistic | mean | p05 | p50 | p95 |", "|---|---|---|---|---|---|"]
    for v, vr in res["variants"].items():
        mc = vr["monte_carlo"]
        if "distributions" in mc:
            for k, nm in (("ev_per_trade_usd", "EV $/trade"), ("total_pnl_usd", "total PnL $"), ("max_drawdown_usd", "max DD $"), ("longest_losing_streak", "longest losing streak")):
                d = mc["distributions"][k]
                L.append(f"| {v} | {nm} | {f(d['mean'])} | {f(d['p05'])} | {f(d['p50'])} | {f(d['p95'])} |")
    if verdict:
        L += ["", "### Development verdict (validation.verdict.classify_development, pooled-by-date)", ""]
        for v, vd in verdict.items():
            L += [f"**{v}: {vd['verdict']}**", "", "Inputs: `" + json.dumps({k: (None if x is None else round(float(x), 5)) for k, x in vd['inputs'].items()}) + "`", ""]
            L += [f"- {r}" for r in vd["reasons"]] + [""]
    return "\n".join(L) + "\n"


def jdefault(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (pd.Timestamp,)):
        return str(o)
    raise TypeError(type(o))


def write_series(out: Path, trades_by_variant: dict) -> None:
    """daily_series.csv (aggregates by NY entry date; cumulative net P&L 'equity' starts at 0, no prices) and trades.csv (local trade log)."""
    import csv
    ny = "America/New_York"
    with open(out / "daily_series.csv", "w", newline="") as fs, open(out / "trades.csv", "w", newline="") as ft:
        ws = csv.writer(fs)
        ws.writerow(["variant", "date", "net_pnl", "gross_pnl", "costs", "n_trades", "equity", "drawdown"])
        wt = csv.writer(ft)
        wt.writerow(["variant", "trade_id", "symbol", "side", "entry_date", "exit_date", "qty", "entry_px", "exit_px", "pnl_gross", "costs", "pnl_net", "r_multiple"])
        for v, t in trades_by_variant.items():
            if len(t) == 0:
                continue
            d = t.assign(_d=t["entry_ts"].dt.tz_convert(ny).dt.strftime("%Y-%m-%d"))
            g = d.groupby("_d").agg(net=("pnl_net", "sum"), gross=("pnl_gross", "sum"), costs=("costs", "sum"), n=("pnl_net", "size")).sort_index()
            eq = g["net"].cumsum()
            dd = eq - np.maximum.accumulate(np.maximum(eq.to_numpy(), 0.0))
            for (day, r), e, k in zip(g.iterrows(), eq, dd):
                ws.writerow([v, day, f"{r.net:.6f}", f"{r.gross:.6f}", f"{r.costs:.6f}", int(r.n), f"{e:.6f}", f"{k:.6f}"])
            for _, r in d.sort_values("entry_ts").iterrows():
                wt.writerow([v, int(r.trade_id), r.symbol, int(r.side), r.entry_ts.tz_convert(ny).strftime("%Y-%m-%d"),
                             r.exit_ts.tz_convert(ny).strftime("%Y-%m-%d"), f"{r.qty:.6f}", f"{r.entry_px:.4f}", f"{r.exit_px:.4f}",
                             f"{r.pnl_gross:.6f}", f"{r.costs:.6f}", f"{r.pnl_net:.6f}", f"{r.r_multiple:.6f}"])


def run_segment(seg, cfg, costs_raw, variants, out=None, series=False):
    out = Path(out) if out else OUT
    _plan(1 + (1 + len(variants)) + (len(variants) if seg == "validation" else 0) + 1)
    costs, bars, split = build(cfg, costs_raw)
    _step("load bars, build chronological split")
    out.mkdir(parents=True, exist_ok=True)
    res, tbv = analyse(seg, cfg, costs, bars, split, variants, progress=True)
    verdict = None
    if seg == "validation":
        verdict = {}
        for v in variants:
            verdict[v] = verdict_inputs(cfg, costs, bars, split, v)
            _step(f"verdict inputs (train+validation): {v}")
    res["verdict"] = verdict
    res["split"] = {"train": [str(x) for x in split.train], "validation": [str(x) for x in split.validation], "purge": str(split.purge)}
    if series:
        write_series(out, tbv)
    (out / f"{seg}_summary.json").write_text(json.dumps(res, indent=1, default=jdefault))
    md = md_report(res, verdict)
    (out / f"{seg}_report.md").write_text(md)
    _step("write summary.json and report.md")
    return md


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--segment", choices=["train", "validation", "test"])
    ap.add_argument("--variant", help="single variant (required for --segment test)")
    ap.add_argument("--freeze", metavar="VARIANT", help="record a freeze for VARIANT (coordinator only)")
    ap.add_argument("--out-dir", help="write outputs here instead of results/daily")
    ap.add_argument("--data-dir", help="directory holding {SYMBOL}_1d.csv (default: configs data_dir)")
    ap.add_argument("--symbols", help="comma-separated subset of the configured universe")
    ap.add_argument("--seed", type=int, help="override baseline/bootstrap/montecarlo seeds")
    ap.add_argument("--progress", action="store_true", help="emit ##PROGRESS lines on stderr")
    a = ap.parse_args(argv)
    cfg, costs_raw = load_cfg()
    _P["on"] = bool(a.progress)
    if a.symbols:
        syms = [x.strip() for x in a.symbols.split(",") if x.strip()]
        bad = [x for x in syms if x not in cfg["universe"]]
        if not syms or bad:
            print(f"REFUSED: --symbols must be a non-empty subset of {cfg['universe']} (got {bad or syms})", file=sys.stderr)
            return 2
        cfg["universe"] = syms
    if a.data_dir:
        cfg["data_dir"] = str(Path(a.data_dir).resolve())
    if a.seed is not None:
        cfg["seeds"] = {k: int(a.seed) for k in cfg["seeds"]}
    if a.freeze:
        rec = G.freeze_config(sid(cfg, a.freeze), config_hash_for(a.freeze, cfg, costs_raw), note="daily H3 freeze")
        print("frozen:", rec["strategy_id"], rec["config_hash"][:12])
        return 0
    if not a.segment:
        ap.error("--segment is required")
    variants = [a.variant] if a.variant else list(cfg["variants"])
    if a.segment == "test":
        if not a.variant or a.variant not in cfg["variants"]:
            print("REFUSED: --segment test needs exactly one --variant (the frozen one).", file=sys.stderr)
            return 2
        try:
            r = guard_test_segment(a.variant, cfg, costs_raw, lambda: run_segment("test", cfg, costs_raw, variants, a.out_dir, series=bool(a.out_dir)))
        except G.FinalTestRefused as e:
            print(str(e), file=sys.stderr)
            return 2
        print(r.banner)
        print(r.result)
        return 0
    print(run_segment(a.segment, cfg, costs_raw, variants, a.out_dir, series=bool(a.out_dir)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
