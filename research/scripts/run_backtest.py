#!/usr/bin/env python
"""Run one backtest variant on a 5-minute bars CSV; logs config + seed + git hash under --out.

Protocol enforcement (CLAUDE.md, docs/REVIEW.md R-01/R-05/R-06):
  * bars are read ONLY through data.loader.load_bars (naive timestamps need --tz-hint) and must pass
    data.quality.quality_report (no error-severity issues) and the contract-label guard;
  * --segment train (default) | validation | test. The split comes from validation.splits.make_split over the
    file's time span; the rows of later segments are DROPPED before anything runs, so train/validation runs
    never see test-period bars; trades are filtered with apply_split (purge + no straddling);
  * test goes ONLY through validation.final_test_guard.evaluate_final_test (needs a prior freeze record; logs
    every access to results/test_access_log.jsonl; refuses SYNTHETIC data);
  * SYNTHETIC is taken from df.attrs (set by the loader from the SYNTHETIC column or filename); validation
    refuses SYNTHETIC data unless --software-test is passed (then the meta says so).
Research only. Results on SYNTHETIC data test software and say nothing about markets.
"""
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys, datetime as dt
from pathlib import Path
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tradelab.backtest import config as cfgmod            # noqa: E402
from tradelab.backtest.engine import run_backtest          # noqa: E402
from tradelab.backtest.guards import check_contract_labels  # noqa: E402
from tradelab.backtest.metrics import summarize            # noqa: E402
from tradelab.backtest.sessions import hhmm, NY            # noqa: E402
from tradelab.data import calendar as cal                  # noqa: E402
from tradelab.data.loader import load_bars                 # noqa: E402
from tradelab.data.quality import quality_report           # noqa: E402
from tradelab.validation import final_test_guard as ftg    # noqa: E402
from tradelab.validation.splits import apply_split, make_split  # noqa: E402

STRATEGY_CONFIGS = {"sweep_reversal": "strategy_sweep_reversal.yaml", "orb": "strategy_orb.yaml",
                    "random_entry": "strategy_random_entry.yaml"}
LOG_PATH = ROOT / "docs" / "EXPERIMENT_LOG.md"


def git_info() -> dict:
    def run(*a):
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"commit": run("rev-parse", "HEAD") or None, "dirty": bool(run("status", "--porcelain"))}


def _kv(items):
    return {k: yaml.safe_load(v) for k, v in (kv.split("=", 1) for kv in items)}


def _signals_frame(res) -> pd.DataFrame:
    return pd.DataFrame([{"strategy_id": s.strategy_id, "side": s.side, "signal_ts": s.signal_ts,
                          "stop_px": s.stop_px, "ref_px": s.ref_px} for s in res.signals],
                        columns=["strategy_id", "side", "signal_ts", "stop_px", "ref_px"])


def _matched_params(sig: pd.DataFrame) -> dict:
    """R-07/R-11: B0 geometry from H1 SIGNAL-time facts (same days, same times of day, |stop-close| distances)."""
    if sig.empty:
        return {"match_days": (), "match_tods": (), "stop_distances_pts": None}
    et = (sig["signal_ts"] - pd.Timedelta(minutes=5)).dt.tz_convert(NY)
    return {"match_days": tuple(sorted({d.isoformat() for d in et.dt.date})),
            "match_tods": tuple(sorted((et.dt.hour * 60 + et.dt.minute).astype(int).tolist())),
            "stop_distances_pts": tuple(sorted((sig["stop_px"] - sig["ref_px"]).abs().round(4).tolist()))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bars", required=True, type=Path)
    ap.add_argument("--strategy", required=True, choices=sorted(STRATEGY_CONFIGS))
    ap.add_argument("--config", type=Path)
    ap.add_argument("--costs", type=Path, default=ROOT / "configs" / "costs_mes.yaml")
    ap.add_argument("--scenario", default="x1", choices=["x1", "x1_5", "x2"])
    ap.add_argument("--set", action="append", default=[], metavar="K=V", help="grid-parameter override")
    ap.add_argument("--segment", default="train", choices=["train", "validation", "test"])
    ap.add_argument("--purge-days", type=float, default=1.0)
    ap.add_argument("--tz-hint", help="IANA tz for NAIVE timestamps (required if the file has none)")
    ap.add_argument("--timestamp-label", default="open", choices=["open", "close"])
    ap.add_argument("--software-test", action="store_true", help="allow SYNTHETIC data in the validation segment")
    ap.add_argument("--match-signals", type=Path, help="B0: signals.csv of the H1 run to match "
                    "(default: B0 runs the baseline H1 cell internally on the same segment)")
    ap.add_argument("--match-set", action="append", default=[], metavar="K=V", help="B0: H1 grid cell to match")
    ap.add_argument("--reps", type=int)
    ap.add_argument("--test-log", type=Path, default=ftg.DEFAULT_LOG)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)

    cfg = cfgmod.load_yaml(a.config or ROOT / "configs" / STRATEGY_CONFIGS[a.strategy])
    inst = cfgmod.INSTRUMENTS[cfg.get("instrument", "MES")]
    overrides = _kv(a.set)

    # ---- load through the tolerant loader; quality gate; contract guard (R-05, R-06)
    bars = load_bars(a.bars, tz_hint=a.tz_hint, timestamp_label=a.timestamp_label, bar_freq="5min")
    synthetic = bool(bars.attrs.get("SYNTHETIC", False))
    check_contract_labels(bars, synthetic)
    qrep = quality_report(bars, "5min", inst)
    if not qrep["ok"]:
        errs = [i for i in qrep["issues"] if i["severity"] == "error"]
        raise SystemExit(f"data quality errors, refusing to run: {errs}")
    if not synthetic:
        print("NOTE: real data. The run must be registered and respect the segment protocol.", file=sys.stderr)
    if a.segment in ("validation", "test") and synthetic and not (a.segment == "validation" and a.software_test):
        raise SystemExit(f"refusing --segment {a.segment} on SYNTHETIC data"
                         + (" (use --software-test for validation)" if a.segment == "validation" else ""))

    # ---- split; drop later segments BEFORE running (R-01)
    split = make_split(bars.index.min(), bars.index.max(), purge=pd.Timedelta(days=a.purge_days))
    seg_start, seg_end = getattr(split, a.segment)
    run_bars = bars if a.segment == "test" else bars[bars.index < seg_end]
    seg_bars = bars[(bars.index >= seg_start) & (bars.index < seg_end)]
    rth = cal.rth_mask(seg_bars.index)
    n_days = int(pd.Series(cal.et_date(seg_bars.index[rth])).nunique())

    def one_run(strategy_cfg, ovr, reps_extra, vsplit=True):
        strat, ecfg, params = cfgmod.build(strategy_cfg, a.costs, a.scenario, ovr or None,
                                           require_logged=LOG_PATH, **reps_extra)
        res = run_backtest(run_bars, strat, ecfg)
        seg = apply_split(res.trades, split).segment(a.segment) if len(res.trades) else res.trades
        sig = _signals_frame(res)
        sig = sig[(sig.signal_ts >= seg_start) & (sig.signal_ts < seg_end)]
        return strat, ecfg, params, res, seg, sig

    def evaluate():
        reps = (a.reps or cfg.get("reps", 1)) if cfg["strategy"] == "random_entry" else 1
        extra = {}
        matched_to = None
        if cfg["strategy"] == "random_entry":
            if a.match_signals is not None:
                sig = pd.read_csv(a.match_signals, parse_dates=["signal_ts"])
                sig = sig[(sig.signal_ts >= seg_start) & (sig.signal_ts < seg_end)]
                matched_to = str(a.match_signals)
            else:
                h1cfg = cfgmod.load_yaml(ROOT / "configs" / STRATEGY_CONFIGS["sweep_reversal"])
                *_, sig = one_run(h1cfg, _kv(a.match_set), {})
                matched_to = cfgmod.variant_id(h1cfg, _kv(a.match_set) or None)
            extra = _matched_params(sig)
        all_tr, summ, last = [], [], None
        for rep in range(reps):
            ex = dict(extra)
            if cfg["strategy"] == "random_entry":
                ex.update(seed=cfg["seed"], rep=rep)
            strat, ecfg, params, res, seg, sig = one_run(cfg, overrides, ex)
            tr = seg.copy()
            if reps > 1:
                tr["rep"] = rep
                tr["uid"] = rep * 1_000_000 + tr["trade_id"]    # R-09: (rep, trade_id) / uid is the key
            all_tr.append(tr)
            summ.append({"rep": rep, **{k: v for k, v in summarize(seg, n_days).items() if k != "exit_reason_counts"}})
            last = (strat, ecfg, params, res, sig)
        return all_tr, summ, last, reps, matched_to

    strat0, ecfg0, _ = cfgmod.build(cfg, a.costs, a.scenario, overrides or None, require_logged=LOG_PATH)
    a.out.mkdir(parents=True, exist_ok=True)
    guard_note = None
    if a.segment == "test":
        h = ftg.config_hash({"cfg": cfg, "overrides": overrides, "scenario": a.scenario,
                             "costs": yaml.safe_load(a.costs.read_text()), "purge_days": a.purge_days,
                             "split": [str(split.train), str(split.validation), str(split.test)]})
        ft = ftg.evaluate_final_test(strat0.name, h, evaluate, log_path=a.test_log, purpose="scripts/run_backtest.py",
                                     data_is_synthetic=synthetic)
        (all_tr, summ, last, reps, matched_to), guard_note = ft.result, ft.banner
        print(guard_note)
    else:
        all_tr, summ, last, reps, matched_to = evaluate()
    strat, ecfg, params, res, sig = last

    trades = pd.concat(all_tr, ignore_index=True)
    trades.to_csv(a.out / "trades.csv", index=False)
    sig.to_csv(a.out / "signals.csv", index=False)
    (a.out / "quality_report.json").write_text(json.dumps(qrep, indent=2, default=str))
    if reps > 1:
        pd.DataFrame(summ).to_csv(a.out / "summary_by_rep.csv", index=False)
    meta = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "git": git_info(),
        "variant_id": strat.name, "strategy_config": cfg, "overrides": overrides,
        "effective_params": {k: (list(v) if isinstance(v, tuple) else v) for k, v in params.items()
                             if k not in ("match_days", "match_tods", "stop_distances_pts")},
        "matched_to": matched_to,
        "cost_config_path": str(a.costs), "cost_scenario": a.scenario, "cost_model": ecfg.cost.__dict__,
        "seed": cfg.get("seed"), "reps": reps, "segment": a.segment,
        "segment_bounds_utc": [str(seg_start), str(seg_end)], "purge_days": a.purge_days,
        "bars_path": str(a.bars), "bars_sha256": hashlib.sha256(a.bars.read_bytes()).hexdigest(),
        "n_bars_loaded": len(bars), "n_bars_used": len(run_bars), "n_trading_days_in_segment": n_days,
        "data_is_synthetic": synthetic, "software_test": bool(a.software_test),
        "quality_ok": qrep["ok"], "quality_issue_codes": sorted({i["code"] for i in qrep["issues"]}),
        "diagnostics_last_rep": dict(res.diagnostics), "final_test_banner": guard_note,
        "summary": summarize(all_tr[0], n_days) if reps == 1 else None,
        "note": "SYNTHETIC data tests software only; it says nothing about markets." if synthetic else "",
    }
    (a.out / "run_meta.json").write_text(json.dumps(meta, indent=2, default=str))
    print(f"wrote {len(trades)} trades ({a.segment}) -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
