#!/usr/bin/env python
"""D4 trend study (DAILY_SPEC2 v1). TRAIN and VALIDATION only; --segment test is refused unconditionally (exit 2).
    python scripts/run_trend.py --segment train
    python scripts/run_trend.py --segment validation
    python scripts/run_trend.py --forward        # 5 days after 2026-10-07 from dataset B; no inference
Outputs aggregates only (no prices) to results/trend/."""
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

from tradelab.daily import report as R  # noqa: E402  (load_bars only)
from tradelab.daily_trend import accounting as A  # noqa: E402
from tradelab.daily_trend import analysis as AN  # noqa: E402
from tradelab.daily_trend import forward as FW  # noqa: E402
from tradelab.validation.splits import make_split  # noqa: E402

OUT = ROOT / "results" / "trend"


def load_cfg():
    return yaml.safe_load((ROOT / "configs" / "trend_strategy.yaml").read_text())


def setup(cfg):
    costs, scen = A.load_costs(ROOT / "configs" / "trend_costs.yaml")
    raw = {s: R.load_bars(s, ROOT / cfg["data_dir_A"]) for s in cfg["universe"]}
    first, last = min(b.index[0] for b in raw.values()), max(b.index[-1] for b in raw.values())
    split = make_split(first, last, tuple(cfg["split"]["fractions"]), pd.Timedelta(days=cfg["split"]["purge_days"]))
    sealed = split.test[0]
    bars = {s: AN.truncate_before(b, sealed) for s, b in raw.items()}      # nothing at/after the sealed start survives
    del raw
    assert all(b.index.max() < sealed for b in bars.values())
    bounds = {"train": (first - pd.Timedelta(days=1), split.validation[0]),
              "validation": (split.validation[0] + split.purge, sealed)}
    return costs, scen, bars, split, bounds


def f4(x):
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.4f}"


def pct(x):
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{100 * x:.2f}%"


def analyse_segment(seg, cfg, costs, scen, bars, bounds):
    start, end = bounds[seg]
    seed, N = cfg["seed"], cfg["n_variants_registered_overall"]
    res = {"segment": seg, "start": str(start), "end_exclusive": str(end), "variants": {}}
    for name, vcfg in cfg["variants"].items():
        frames = {s: AN.symbol_frame(b, vcfg) for s, b in bars.items()}
        dates = AN.common_dates(frames, start, end)
        win = AN.window(frames, dates, costs)
        vr = {"n_days": len(dates), "first_date": str(dates[0].date()), "last_date": str(dates[-1].date()), "scopes": {}}
        scopes = dict(win)
        scopes["pooled"] = {"r": AN.pooled(win, "r"), "r_bh": AN.pooled(win, "r_bh"), "p": np.mean([win[s]["p"] for s in win], axis=0),
                            "pp": None}
        for sc, w in scopes.items():
            if sc == "pooled":
                m = AN.segment_metrics(w["r"], w["r_bh"], (w["p"] > 0).astype(int), 0)
                m["time_in_market"] = float(w["p"].mean())
                m.pop("buys"), m.pop("sells"), m.pop("switches"), m.pop("round_trips")
            else:
                m = AN.segment_metrics(w["r"], w["r_bh"], w["p"], w["pp"][0])
            d = w["r"] - w["r_bh"]
            m["boot"] = {b: AN.bootstrap_mean_diff(d, b, cfg["bootstrap"]["n_resamples"], seed) for b in cfg["bootstrap"]["blocks"]}
            m["p_bonferroni_N"] = min(1.0, m["boot"][10]["p_two_sided"] * N)
            vr["scopes"][sc] = m
        # cost stress (pooled + total only)
        cs = {}
        for k, mult in scen.items():
            w2 = AN.window(frames, dates, costs.scaled(mult))
            r, rb = AN.pooled(w2, "r"), AN.pooled(w2, "r_bh")
            cs[k] = {"mult": mult, "total": float(A.total_return(r)), "total_bh": float(A.total_return(rb)),
                     "mean_diff_bps": float((r - rb).mean() * 1e4), "ci": [AN.bootstrap_mean_diff(r - rb, 10, cfg["bootstrap"]["n_resamples"], seed)[x] * 1e4 for x in ("lo", "hi")]}
        vr["cost_stress"] = cs
        # B_R null
        null = AN.random_timing_null(win, costs, seed, cfg["b_r_null"]["n_draws"])
        vr["null"] = {}
        for sc in list(win) + ["pooled"]:
            m = vr["scopes"][sc]
            if sc != "pooled" and win[sc]["p"].min() == win[sc]["p"].max():
                vr["null"][sc] = None
                continue
            r = scopes[sc]["r"]
            obs = {"total": m["total"], "mdd": m["mdd"], "retvol": m["total"] / m["vol"] if m["vol"] > 0 else float("nan")}
            vr["null"][sc] = AN.null_compare(obs, null[sc])
        # Monte Carlo
        vr["mc"] = {"pooled": {b: AN.monte_carlo(scopes["pooled"]["r"], b, cfg["mc"]["n_paths"], seed, cfg["mc"]["dd_thresholds"]) for b in cfg["mc"]["blocks"]},
                    "pooled_B_H": AN.monte_carlo(scopes["pooled"]["r_bh"], 10, cfg["mc"]["n_paths"], seed, cfg["mc"]["dd_thresholds"]),
                    **{s: AN.monte_carlo(win[s]["r"], 10, cfg["mc"]["n_paths"], seed, cfg["mc"]["dd_thresholds"]) for s in win}}
        res["variants"][name] = vr
    return res


def md_segment(res, verdicts=None):
    L = [f"## Segment: {res['segment']}  ({res['start'][:10]} .. <{res['end_exclusive'][:10]})", ""]
    for name, vr in res["variants"].items():
        L += [f"### {name}  ({vr['n_days']} days {vr['first_date']}..{vr['last_date']})", "",
              "| scope | total ret | B_H total | MDD | B_H MDD | DD reduction | vol ann | B_H vol | time in mkt | buys | sells (round trips) | mean daily diff vs B_H (bps) | 95% CI blk10 | blk5 | blk20 | Bonf p (N=19) |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for sc, m in vr["scopes"].items():
            b = m["boot"]
            ci = lambda k: f"[{b[k]['lo'] * 1e4:.2f}, {b[k]['hi'] * 1e4:.2f}]"
            L.append(f"| {sc} | {pct(m['total'])} | {pct(m['total_bh'])} | {pct(m['mdd'])} | {pct(m['mdd_bh'])} | {f4(m['dd_reduction'])} | {pct(m['vol'])} | {pct(m['vol_bh'])} | {f4(m['time_in_market'])} | "
                     f"{m.get('buys', 'n/a')} | {m.get('sells', 'n/a')} | {b[10]['mean'] * 1e4:.2f} | {ci(10)} | {ci(5)} | {ci(20)} | {f4(m['p_bonferroni_N'])} |")
        L += ["", "Cost stress (pooled):", "", "| cost | total ret | B_H total | mean daily diff (bps) | CI blk10 (bps) |", "|---|---|---|---|---|"]
        for k, c in vr["cost_stress"].items():
            L.append(f"| x{c['mult']} | {pct(c['total'])} | {pct(c['total_bh'])} | {c['mean_diff_bps']:.2f} | [{c['ci'][0]:.2f}, {c['ci'][1]:.2f}] |")
        L += ["", "B_R random-timing null (10,000 draws, seed 20261014; 'pct' = share of null draws the strategy beats; >=0.95 flags beating the 95th percentile; does not change the label):", "",
              "| scope | total: obs / null median / null p95 / pct | MDD: obs / null median / null p05 / pct (better=lower) | return/vol pct |", "|---|---|---|---|"]
        for sc, n in vr["null"].items():
            if n is None:
                L.append(f"| {sc} | always in/out of market: no shuffle possible | | |")
                continue
            t, d, rv = n["total"], n["mdd"], n["retvol"]
            L.append(f"| {sc} | {pct(t['obs'])} / {pct(t['null_p50'])} / {pct(t['null_p95'])} / {t['pct']:.3f} | {pct(d['obs'])} / {pct(d['null_p50'])} / {pct(d['null_p05'])} / {d['pct']:.3f} | {f4(rv['pct'])} |")
        L += ["", "Monte Carlo day-block bootstrap of daily returns (5000 paths, horizon = segment length, seed 20261014). Resamples the same evidence; adds no independent market evidence:", "",
              "| series | block | total ret p05/p50/p95 | max DD p05/p50/p95 | P(MDD>15%) | P(MDD>25%) |", "|---|---|---|---|---|---|"]
        rows = [("pooled", b, vr["mc"]["pooled"][b]) for b in vr["mc"]["pooled"]] + [("pooled B_H", 10, vr["mc"]["pooled_B_H"])] + [(s, 10, vr["mc"][s]) for s in ("SPY", "QQQ", "IWM")]
        for nm, b, m in rows:
            tr, dd = m["total_return"], m["max_drawdown"]
            L.append(f"| {nm} | {b} | {pct(tr['p05'])} / {pct(tr['p50'])} / {pct(tr['p95'])} | {pct(dd['p05'])} / {pct(dd['p50'])} / {pct(dd['p95'])} | {m['p_mdd_gt_15']:.3f} | {m['p_mdd_gt_25']:.3f} |")
        L.append("")
    return "\n".join(L) + "\n"


def run_forward(cfg, costs):
    bars_a = {s: R.load_bars(s, ROOT / cfg["data_dir_A"]) for s in cfg["universe"]}
    bars_b = {s: R.load_bars(s, ROOT / cfg["data_dir_B"]) for s in cfg["universe"]}
    fw = FW.forward_observation(bars_a, bars_b, cfg["variants"], costs)
    L = ["## Forward observation (dataset B, the 5 days after 2026-10-07). NO INFERENCE: 5 days.", "",
         "Position = held during that day (signal from the prior close). Return = daily net return of the 1-ETF strategy; B_H = held since before (no buy cost). Equal-weight pooled shown last.", ""]
    for name, per in fw.items():
        dfs = list(per.values())
        L += [f"### {name}", "", "| date | " + " | ".join(f"{s} pos | {s} ret | {s} B_H" for s in per) + " | pooled ret | pooled B_H |", "|---|" + "---|" * (3 * len(per) + 2)]
        for i, d in enumerate(dfs[0].index):
            cells = []
            for s, df in per.items():
                cells += [str(int(df['pos'].iloc[i])), pct(df['ret'].iloc[i]), pct(df['ret_bh'].iloc[i])]
            L.append(f"| {d} | " + " | ".join(cells) + f" | {pct(np.mean([x['ret'].iloc[i] for x in dfs]))} | {pct(np.mean([x['ret_bh'].iloc[i] for x in dfs]))} |")
        cum = lambda k: np.prod([1 + np.mean([x[k].iloc[i] for x in dfs]) for i in range(len(dfs[0]))]) - 1
        L += ["", f"Pooled 5-day compounded: strategy {pct(cum('ret'))}, B_H {pct(cum('ret_bh'))}.", ""]
    md = "\n".join(L) + "\n"
    (OUT / "forward_report.md").write_text(md)
    return md


def jd(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    raise TypeError(type(o))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--segment", choices=["train", "validation", "test"])
    ap.add_argument("--forward", action="store_true")
    a = ap.parse_args(argv)
    if a.segment == "test":
        print("REFUSED: the sealed test segment is never computed by run_trend.py.", file=sys.stderr)
        return 2
    if not a.segment and not a.forward:
        ap.error("need --segment or --forward")
    cfg = load_cfg()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.forward:
        costs, _ = A.load_costs(ROOT / "configs" / "trend_costs.yaml")
        print(run_forward(cfg, costs))
        return 0
    costs, scen, bars, split, bounds = setup(cfg)
    res = analyse_segment(a.segment, cfg, costs, scen, bars, bounds)
    (OUT / f"{a.segment}_summary.json").write_text(json.dumps(res, indent=1, default=jd))
    md = md_segment(res)
    (OUT / f"{a.segment}_report.md").write_text(md)
    print(md)
    return 0


def verdict_report():
    """Combine train+validation summaries into the pre-registered verdict (pooled)."""
    cfg = load_cfg()
    tr = json.loads((OUT / "train_summary.json").read_text())
    va = json.loads((OUT / "validation_summary.json").read_text())
    L = ["## Pre-registered verdict (pooled equal-weight; REJECTED iff BOTH segments have total < B_H AND DD reduction < 25%)", ""]
    for name in cfg["variants"]:
        v, notes = AN.verdict({"train": tr["variants"][name]["scopes"]["pooled"], "validation": va["variants"][name]["scopes"]["pooled"]}, cfg["reject_dd_reduction_below"])
        L += [f"**{name}: {v}**", ""] + [f"- {n}" for n in notes] + [""]
    md = "\n".join(L) + "\n"
    (OUT / "verdict.md").write_text(md)
    return md


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--verdict":
        print(verdict_report())
        sys.exit(0)
    sys.exit(main())
