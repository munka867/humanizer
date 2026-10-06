#!/usr/bin/env python
"""Run Monte Carlo + cost stress on a REAL trade table produced by the backtester.

Usage: python scripts/run_montecarlo.py --trades results/trades_validation.csv --seed 20261006 \
         [--method day_block] [--n-sims 5000] [--account 600] [--margin-per-contract X] \
         [--daily-loss-limit 30] [--max-dd-limit 150] [--out results/mc_validation.json]
Refuses to run without a trades file (it never fabricates trades). Prints the caveat."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pandas as pd  # noqa: E402

from tradelab.contracts import Instrument  # noqa: E402
from tradelab.validation.montecarlo import CAVEAT, MCConfig, cost_stress, run_montecarlo  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--method", default="day_block", choices=["iid", "trade_block", "day_block"])
    ap.add_argument("--n-sims", type=int, default=2000)
    ap.add_argument("--block-len", type=int, default=5)
    ap.add_argument("--account", type=float, default=600.0)
    ap.add_argument("--margin-per-contract", type=float, default=None)
    ap.add_argument("--daily-loss-limit", type=float, default=None)
    ap.add_argument("--max-dd-limit", type=float, default=None)
    ap.add_argument("--fill-miss", type=float, default=0.0)
    ap.add_argument("--worse-stop-ticks", type=float, default=0.0)
    ap.add_argument("--symbol", default="MES")
    ap.add_argument("--point-value", type=float, default=5.0)
    ap.add_argument("--tick-size", type=float, default=0.25)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    p = Path(a.trades)
    if not p.exists():
        print(f"ERROR: trades file {p} not found. Run the backtester first; nothing is fabricated.", file=sys.stderr)
        return 2
    df = pd.read_csv(p, parse_dates=["signal_ts", "entry_ts", "exit_ts"]) if p.suffix == ".csv" else pd.read_parquet(p)
    for c in ("signal_ts", "entry_ts", "exit_ts"):
        df[c] = pd.to_datetime(df[c], utc=True)
    inst = Instrument(a.symbol, a.point_value, a.tick_size)
    cfg = MCConfig(seed=a.seed, method=a.method, n_sims=a.n_sims, block_len=a.block_len, account_size=a.account,
                   daily_loss_limit=a.daily_loss_limit, max_dd_limit=a.max_dd_limit, fill_miss_frac=a.fill_miss,
                   worse_stop_ticks=a.worse_stop_ticks, margin_per_contract_usd=a.margin_per_contract, instrument=inst)
    res = run_montecarlo(df, cfg)
    cs = cost_stress(df, inst, seed=a.seed)
    res["cost_stress"] = cs.to_dict("records")
    print(json.dumps(res, indent=2, default=str))
    print("\nCOST STRESS\n" + cs.to_string(index=False))
    print("\nCAVEAT: " + CAVEAT)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
