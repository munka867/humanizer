#!/usr/bin/env python
"""Run one backtest variant on a bars file; logs config + seed + git hash under --out.

Research only. Do not point this at real data until a split/protocol is frozen (CLAUDE.md); the final
test set must be accessed through the validation module's access log, not here.
"""
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys, datetime as dt
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from tradelab.backtest import config as cfgmod            # noqa: E402
from tradelab.backtest.engine import run_backtest          # noqa: E402
from tradelab.backtest.metrics import summarize            # noqa: E402

STRATEGY_CONFIGS = {"sweep_reversal": "strategy_sweep_reversal.yaml", "orb": "strategy_orb.yaml",
                    "random_entry": "strategy_random_entry.yaml"}


def git_info() -> dict:
    def run(*a):
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"commit": run("rev-parse", "HEAD") or None, "dirty": bool(run("status", "--porcelain"))}


def load_bars(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    if "ts_open" in df.columns:
        df["ts_open"] = pd.to_datetime(df["ts_open"], utc=True)
        df = df.set_index("ts_open")
    df.index = pd.DatetimeIndex(df.index).tz_convert("UTC")
    df.index.name = "ts_open"
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True, type=Path)
    ap.add_argument("--strategy", required=True, choices=sorted(STRATEGY_CONFIGS))
    ap.add_argument("--config", type=Path, help="strategy YAML (default: configs/strategy_<name>.yaml)")
    ap.add_argument("--costs", type=Path, default=ROOT / "configs" / "costs_mes.yaml")
    ap.add_argument("--scenario", default="x1", choices=["x1", "x1_5", "x2"])
    ap.add_argument("--set", action="append", default=[], metavar="K=V", help="grid-parameter override")
    ap.add_argument("--match-trades", type=Path, help="B0: H1 trades.csv whose stop distances are sampled")
    ap.add_argument("--reps", type=int, help="B0 replications (default from config)")
    ap.add_argument("--out", required=True, type=Path, help="output directory (e.g. results/<run_id>)")
    a = ap.parse_args(argv)

    cfg = cfgmod.load_yaml(a.config or ROOT / "configs" / STRATEGY_CONFIGS[a.strategy])
    overrides = {}
    for kv in a.set:
        k, v = kv.split("=", 1)
        overrides[k] = yaml_scalar(v)
    bars = load_bars(a.bars)
    synthetic = "SYNTHETIC" in a.bars.name.upper()
    if not synthetic:
        print("WARNING: bars file is not labelled SYNTHETIC. Make sure this run is registered in "
              "docs/EXPERIMENT_LOG.md and respects the train/validation/test protocol.", file=sys.stderr)

    extra = {}
    if a.match_trades is not None:
        t = pd.read_csv(a.match_trades)
        extra["stop_distances_pts"] = tuple(sorted(((t["risk_usd"] / t["qty"]) / cfgmod.INSTRUMENTS[cfg.get("instrument", "MES")].point_value).round(4)))
    reps = (a.reps or cfg.get("reps", 1)) if cfg["strategy"] == "random_entry" else 1

    all_trades, rep_summ = [], []
    for rep in range(reps):
        ex = dict(extra)
        if cfg["strategy"] == "random_entry":
            ex.update(seed=cfg["seed"], rep=rep)
        strat, ecfg, params = cfgmod.build(cfg, a.costs, a.scenario, overrides or None, **ex)
        res = run_backtest(bars, strat, ecfg)
        tr = res.trades.copy()
        if reps > 1:
            tr["rep"] = rep
        all_trades.append(tr)
        rep_summ.append({"rep": rep, **{k: v for k, v in summarize(res.trades).items() if k != "exit_reason_counts"}})
    trades = pd.concat(all_trades, ignore_index=True)

    a.out.mkdir(parents=True, exist_ok=True)
    trades.to_csv(a.out / "trades.csv", index=False)
    if reps > 1:
        pd.DataFrame(rep_summ).to_csv(a.out / "summary_by_rep.csv", index=False)
    meta = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "git": git_info(),
        "strategy_config": cfg, "overrides": overrides, "effective_params": {k: (list(v) if isinstance(v, tuple) else v) for k, v in params.items()},
        "cost_config_path": str(a.costs), "cost_scenario": a.scenario, "cost_model": ecfg.cost.__dict__,
        "seed": cfg.get("seed"), "reps": reps, "bars_path": str(a.bars),
        "bars_sha256": hashlib.sha256(a.bars.read_bytes()).hexdigest(), "n_bars": len(bars),
        "data_is_synthetic": synthetic, "diagnostics_last_rep": dict(res.diagnostics),
        "summary": summarize(res.trades) if reps == 1 else None,
        "note": "Results on SYNTHETIC data test software only and say nothing about markets." if synthetic else "",
    }
    (a.out / "run_meta.json").write_text(json.dumps(meta, indent=2, default=str))
    print(f"wrote {len(trades)} trades -> {a.out}")
    return 0


def yaml_scalar(v: str):
    import yaml
    return yaml.safe_load(v)


if __name__ == "__main__":
    raise SystemExit(main())
