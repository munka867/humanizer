"""Segment frames, metrics, block bootstrap, run-length-shuffle null, Monte Carlo."""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradelab.daily_trend import accounting as A
from tradelab.daily_trend.signals import make_signal


def truncate_before(bars: pd.DataFrame, sealed_start) -> pd.DataFrame:
    """Drop every bar at/after the sealed start BEFORE anything is computed."""
    out = bars[bars.index < sealed_start]
    assert len(out) == 0 or out.index.max() < sealed_start
    return out


def symbol_frame(bars: pd.DataFrame, kind_cfg: dict) -> pd.DataFrame:
    """Per-day frame: o, c, pc, s (signal at close t), p (position during t = s_{t-1}), pp, elig (t >= first_valid+1)."""
    s, first = make_signal(kind_cfg, bars["close"])
    n = len(bars)
    p = np.concatenate([[0], s[:-1]]) if n else np.array([], dtype=int)
    pp = np.concatenate([[0], p[:-1]]) if n else p
    elig = np.arange(n) >= first + 1
    return pd.DataFrame({"o": bars["open"].to_numpy(), "c": bars["close"].to_numpy(),
                         "pc": bars["close"].shift(1).to_numpy(), "s": s, "p": p, "pp": pp, "elig": elig}, index=bars.index)


def common_dates(frames: dict, start, end) -> pd.DatetimeIndex:
    idx = None
    for f in frames.values():
        m = f.index[(f["elig"].to_numpy()) & (f.index >= start) & (f.index < end)]
        idx = m if idx is None else idx.intersection(m)
    return idx.sort_values()


def window(frames: dict, dates, costs: A.TrendCosts) -> dict:
    """Per symbol: strategy returns, B_H returns (buy at first window open, same cost), position arrays."""
    out = {}
    for sym, f in frames.items():
        w = f.loc[dates]
        o, c, pc = w["o"].to_numpy(), w["c"].to_numpy(), w["pc"].to_numpy()
        p, pp = w["p"].to_numpy(), w["pp"].to_numpy()
        r = A.daily_returns(o, c, pc, p, pp, costs)
        ones = np.ones(len(w), dtype=int)
        pp_bh = np.concatenate([[0], ones[:-1]])
        rbh = A.daily_returns(o, c, pc, ones, pp_bh, costs)
        out[sym] = {"o": o, "c": c, "pc": pc, "p": p, "pp": pp, "r": r, "r_bh": rbh}
    return out


def pooled(win: dict, key: str) -> np.ndarray:
    return np.mean([win[s][key] for s in win], axis=0)


def switch_counts(p, pp0) -> dict:
    prev = np.concatenate([[pp0], p[:-1]])
    buys, sells = int(((prev == 0) & (p == 1)).sum()), int(((prev == 1) & (p == 0)).sum())
    return {"buys": buys, "sells": sells, "switches": buys + sells, "round_trips": sells}


def block_indices(n: int, block: int, n_paths: int, rng) -> np.ndarray:
    b = min(block, n)
    nb = -(-n // b)
    starts = rng.integers(0, n - b + 1, size=(n_paths, nb))
    return (starts[:, :, None] + np.arange(b)).reshape(n_paths, nb * b)[:, :n]


def bootstrap_mean_diff(d: np.ndarray, block: int, n: int, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    idx = block_indices(len(d), block, n, rng)
    means = d[idx].mean(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    p_le, p_ge = (means <= 0).mean(), (means >= 0).mean()
    p2 = float(min(1.0, 2 * min(p_le, p_ge) + 1.0 / (n + 1)))
    return {"mean": float(d.mean()), "lo": float(lo), "hi": float(hi), "block": block, "p_two_sided": p2}


def run_structure(p: np.ndarray):
    """(first_state, on_lengths, off_lengths)."""
    chg = np.flatnonzero(np.diff(p) != 0) + 1
    bounds = np.concatenate([[0], chg, [len(p)]])
    lens = np.diff(bounds)
    states = p[bounds[:-1]]
    return int(p[0]), lens[states == 1], lens[states == 0]


def shuffle_runs(first: int, on: np.ndarray, off: np.ndarray, rng) -> np.ndarray:
    """Permute on-run lengths and off-run lengths separately; keep the first state and alternate. Preserves time in
    market, number of switches and the first state."""
    on, off = rng.permutation(on), rng.permutation(off)
    a, b = (on, off) if first == 1 else (off, on)
    lens, st = [], []
    for i in range(len(a) + len(b)):
        src = a if i % 2 == 0 else b
        lens.append(src[i // 2])
        st.append(first if i % 2 == 0 else 1 - first)
    return np.repeat(np.array(st, dtype=int), np.array(lens, dtype=int))


def _stats_matrix(r: np.ndarray) -> dict:
    tot, mdd, vol = A.total_return(r), A.max_drawdown(r), A.ann_vol(r)
    with np.errstate(divide="ignore", invalid="ignore"):
        rv = np.where(vol > 0, tot / vol, np.nan)
    return {"total": tot, "mdd": mdd, "retvol": rv}


def random_timing_null(win: dict, costs: A.TrendCosts, seed: int, n_draws: int, chunk: int = 2000) -> dict:
    """B_R: per ETF shuffle the strategy's own on/off run lengths; pooled = equal-weight average of the shuffled ETFs."""
    rng = np.random.default_rng(seed)
    syms = sorted(win)
    struct = {s: run_structure(win[s]["p"]) for s in syms}
    acc = {k: {m: [] for m in ("total", "mdd", "retvol")} for k in syms + ["pooled"]}
    for lo in range(0, n_draws, chunk):
        m = min(chunk, n_draws - lo)
        rets = {}
        for s in syms:
            w = win[s]
            P = np.stack([shuffle_runs(*struct[s], rng) for _ in range(m)])
            pp0 = w["pp"][0]
            PP = np.concatenate([np.full((m, 1), pp0), P[:, :-1]], axis=1)
            rets[s] = A.daily_returns(w["o"], w["c"], w["pc"], P, PP, costs)
            for k, v in _stats_matrix(rets[s]).items():
                acc[s][k].append(v)
        for k, v in _stats_matrix(np.mean([rets[s] for s in syms], axis=0)).items():
            acc["pooled"][k].append(v)
    return {k: {m: np.concatenate(v) for m, v in d.items()} for k, d in acc.items()}


def _mid(better, tie) -> float:
    """Share of null draws beaten, ties counted half."""
    return float(better.mean() + 0.5 * tie.mean())


def null_compare(obs: dict, null: dict) -> dict:
    """obs: total, mdd, retvol. Percentiles: share of null draws the strategy beats (higher return / lower MDD / higher ret-vol)."""
    return {"total": {"obs": float(obs["total"]), "pct": _mid(null["total"] < obs["total"], null["total"] == obs["total"]), "null_p50": float(np.nanmedian(null["total"])), "null_p95": float(np.nanquantile(null["total"], .95))},
            "mdd": {"obs": float(obs["mdd"]), "pct": _mid(null["mdd"] > obs["mdd"], null["mdd"] == obs["mdd"]), "null_p50": float(np.nanmedian(null["mdd"])), "null_p05": float(np.nanquantile(null["mdd"], .05))},
            "retvol": {"obs": float(obs["retvol"]), "pct": _mid(null["retvol"] < obs["retvol"], null["retvol"] == obs["retvol"]), "null_p50": float(np.nanmedian(null["retvol"])), "null_p95": float(np.nanquantile(null["retvol"], .95))}}


def monte_carlo(r: np.ndarray, block: int, n_paths: int, seed: int, dd_thresholds=(0.15, 0.25)) -> dict:
    rng = np.random.default_rng(seed)
    idx = block_indices(len(r), block, n_paths, rng)
    paths = r[idx]
    tot, mdd = A.total_return(paths), A.max_drawdown(paths)
    q = lambda x: {k: float(v) for k, v in zip(("p05", "p50", "p95"), np.quantile(x, [.05, .5, .95]))}
    return {"block": block, "seed": seed, "n_paths": n_paths, "horizon": len(r), "total_return": q(tot), "max_drawdown": q(mdd),
            **{f"p_mdd_gt_{int(t * 100)}": float((mdd > t).mean()) for t in dd_thresholds}}


def segment_metrics(r, r_bh, p, pp0) -> dict:
    mdd, mdd_bh = float(A.max_drawdown(r)), float(A.max_drawdown(r_bh))
    return {"n_days": len(r), "total": float(A.total_return(r)), "total_bh": float(A.total_return(r_bh)),
            "mdd": mdd, "mdd_bh": mdd_bh, "dd_reduction": float(1 - mdd / mdd_bh) if mdd_bh > 0 else float("nan"),
            "vol": float(A.ann_vol(r)), "vol_bh": float(A.ann_vol(r_bh)), "time_in_market": float(np.mean(p)),
            **switch_counts(p, pp0)}


def verdict(per_segment: dict, reject_dd=0.25) -> tuple[str, list[str]]:
    """REJECTED iff in BOTH segments pooled total < B_H and drawdown reduction < reject_dd. Never SUITABLE."""
    bad, notes = [], []
    for seg, m in per_segment.items():
        worse = m["total"] < m["total_bh"]
        dd_low = (m["dd_reduction"] == m["dd_reduction"]) and m["dd_reduction"] < reject_dd
        bad.append(worse and dd_low)
        notes.append(f"{seg}: total {m['total']:+.4f} vs B_H {m['total_bh']:+.4f} ({'below' if worse else 'not below'}); "
                     f"drawdown reduction {m['dd_reduction']:.3f} ({'<' if dd_low else '>='} {reject_dd})")
    if all(bad):
        return "REJECTED", notes
    return "INCONCLUSIVE", notes + ["not rejected under the pre-registered rule; SUITABLE is unavailable (round trips << 30 per segment)"]
