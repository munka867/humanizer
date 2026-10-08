"""B_D0 (every eligible day long open->close) and B_D1 (matched random-day permutation null).
The null is conditional on the sample, drawn per instrument with the strategy's own long/short counts."""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradelab.daily.engine import DailyCosts, make_trades

SEED_DEFAULT = 20261008


def b_d0_trades(cands: pd.DataFrame, costs: DailyCosts, strategy: str = "B_D0_long_every_day") -> pd.DataFrame:
    return make_trades(cands, +1, strategy, costs)


def bps(trades: pd.DataFrame) -> np.ndarray:
    return (trades["pnl_net"] / (trades["qty"] * trades["entry_px"])).to_numpy(float) * 1e4


def matched_permutation_null(cands_by_symbol: dict, counts: dict, costs: DailyCosts, seed: int = SEED_DEFAULT,
                             n_draws: int = 10000) -> dict:
    """counts: {symbol: (n_long, n_short)}. For each instrument, draw n_long+n_short DISTINCT eligible days
    uniformly at random (first n_long traded long, rest short), same costs/sizing. Pooled statistic per draw =
    mean over all drawn trades of net USD / net R / net bps. Returns arrays of length n_draws."""
    rng = np.random.default_rng(seed)
    sums = {k: np.zeros(n_draws) for k in ("usd", "r", "bps")}
    n_total = 0
    for sym in sorted(cands_by_symbol):
        c = cands_by_symbol[sym]
        nl, ns = counts.get(sym, (0, 0))
        if nl + ns == 0:
            continue
        if nl + ns > len(c):
            raise ValueError(f"{sym}: {nl + ns} signals but only {len(c)} eligible days")
        tl, ts = make_trades(c, +1, "x", costs), make_trades(c, -1, "x", costs)
        vals = {"usd": (tl["pnl_net"].to_numpy(), ts["pnl_net"].to_numpy()),
                "r": (tl["r_multiple"].to_numpy(), ts["r_multiple"].to_numpy()),
                "bps": (bps(tl), bps(ts))}
        order = rng.random((n_draws, len(c))).argsort(axis=1)
        li, si = order[:, :nl], order[:, nl:nl + ns]
        for k, (vl, vs) in vals.items():
            sums[k] += vl[li].sum(axis=1) + vs[si].sum(axis=1)
        n_total += nl + ns
    if n_total == 0:
        return {"n": 0, "usd": np.array([]), "r": np.array([]), "bps": np.array([]), "seed": seed}
    out = {k: v / n_total for k, v in sums.items()}
    out.update(n=n_total, seed=seed, n_draws=n_draws)
    return out


def null_summary(null: dict, observed: dict) -> dict:
    """observed: {'usd','r','bps'} strategy means. p one-sided in the signal's favour (null >= observed),
    with +1 smoothing; percentile = share of null draws <= observed."""
    res = {"n": null["n"], "seed": null["seed"]}
    for k in ("usd", "r", "bps"):
        d = null[k]
        if len(d) == 0:
            res[k] = None
            continue
        o = observed[k]
        res[k] = {"observed": float(o), "null_mean": float(d.mean()), "null_p05": float(np.quantile(d, .05)),
                  "null_p50": float(np.quantile(d, .5)), "null_p95": float(np.quantile(d, .95)),
                  "p_one_sided": float((1 + (d >= o).sum()) / (len(d) + 1)), "percentile": float((d <= o).mean())}
    return res
