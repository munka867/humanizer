"""Seeded Monte Carlo on a trade table.

EVERY output carries CAVEAT: these simulations only RESAMPLE the observed trades. They add
no independent market evidence, say nothing about regimes not in the sample, and are only as
good as (a) the backtest's fills/costs and (b) the sample's representativeness. A tight
simulated distribution of a biased sample is still biased.

Methods (MCConfig.method)
  "iid"          resample trades independently (destroys all dependence; optimistic on
                 streaks/drawdown clustering). horizon = number of trades.
  "trade_block"  moving-block bootstrap of consecutive trades, length block_len.
  "day_block"    (default) resample whole DAYS with replacement, keeping the within-day
                 trade sequence and dependence. horizon = number of days.
Daily-loss-limit breach probability is only defined for "day_block" (otherwise None).

Stresses (applied on top of any method)
  worse_stop_ticks  deterministic: each stop/ambiguous_stop exit loses extra ticks
                    (ticks * tick_size * point_value * qty). Costs/prices are re-derived from
                    the trade's qty; the engine is NOT re-run, so path-dependent effects
                    (a worse stop fill changing later entries) are not captured.
  fill_miss_frac    per simulated path, each winning trade is dropped with this probability
                    (limit orders that would have filled in a backtest but not live; the
                    dropped trades are exactly the favourable ones, i.e. adverse selection).
  cost_stress()     deterministic re-pricing for cost multipliers and extra slippage ticks.

Account constraint: MES needs margin per contract (broker-set, changes over time; a PARAMETER
here, never asserted by this module). A $600 account is dominated by margin and by the risk of
a single stop; see account_feasibility(). Ruin here = closed-trade equity <= ruin_equity.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from tradelab.contracts import Instrument
from tradelab.validation.stats import (bootstrap_mean_ci_by_day, day_codes, longest_losing_streak,
                                       max_drawdown)

CAVEAT = ("Monte Carlo resamples the same evidence; it adds no independent market evidence. "
          "Results depend entirely on the input trades (their fills, costs, and sample period) "
          "and on the resampling assumptions.")
METHODS = ("iid", "trade_block", "day_block")


@dataclass(frozen=True)
class MCConfig:
    seed: int                                   # explicit, required
    method: str = "day_block"
    n_sims: int = 2000
    horizon: int | None = None                  # trades (iid/trade_block) or days (day_block); default = sample size
    block_len: int = 5
    account_size: float = 600.0
    ruin_equity: float = 0.0
    daily_loss_limit: float | None = None       # USD, positive number
    max_dd_limit: float | None = None           # USD, positive number
    fill_miss_frac: float = 0.0
    worse_stop_ticks: float = 0.0
    margin_per_contract_usd: float | None = None
    instrument: Instrument | None = None


def check_trades(trades: pd.DataFrame, tol: float = 1e-6) -> None:
    need = ["entry_ts", "exit_ts", "pnl_net"]
    miss = [c for c in need if c not in trades.columns]
    if miss:
        raise ValueError(f"trades missing {miss}")
    if {"pnl_gross", "costs"} <= set(trades.columns) and len(trades):
        err = (trades["pnl_gross"] - trades["costs"] - trades["pnl_net"]).abs().max()
        if err > tol:
            raise ValueError(f"pnl_net != pnl_gross - costs (max abs err {err})")
        if (trades["costs"] < 0).any():
            raise ValueError("costs must be non-negative")


def _prep(trades: pd.DataFrame) -> pd.DataFrame:
    check_trades(trades)
    return trades.sort_values(["entry_ts", "exit_ts"], kind="stable").reset_index(drop=True)


def _tick_usd(inst: Instrument | None) -> float:
    if inst is None:
        raise ValueError("instrument required for tick-based stresses")
    return inst.tick_size * inst.point_value


def apply_worse_stop(trades: pd.DataFrame, ticks: float, inst: Instrument | None) -> pd.DataFrame:
    if ticks == 0 or len(trades) == 0:
        return trades
    extra = ticks * _tick_usd(inst) * trades["qty"].to_numpy(dtype=float)
    is_stop = trades["exit_reason"].isin(["stop", "ambiguous_stop"]).to_numpy()
    out = trades.copy()
    out["pnl_net"] = trades["pnl_net"].to_numpy(dtype=float) - np.where(is_stop, extra, 0.0)
    return out


def draw_paths(trades: pd.DataFrame, method: str, n_sims: int, horizon: int | None, block_len: int,
               rng: np.random.Generator) -> list[tuple[np.ndarray, np.ndarray | None]]:
    """Return per-simulation (trade_index_array, draw_position_array_or_None) into the
    entry-sorted trade table. For day_block the second array gives, per trade, which sampled
    day (0..horizon-1) it came from, so within-day grouping is recoverable."""
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    n = len(trades)
    if n == 0:
        return [(np.array([], dtype=int), None) for _ in range(n_sims)]
    if method == "iid":
        H = horizon or n
        idx = rng.integers(0, n, size=(n_sims, H))
        return [(row, None) for row in idx]
    if method == "trade_block":
        H = horizon or n
        L = max(1, min(block_len, n))
        nb = -(-H // L)
        starts = rng.integers(0, n - L + 1, size=(n_sims, nb))
        idx = (starts[:, :, None] + np.arange(L)).reshape(n_sims, nb * L)[:, :H]
        return [(row, None) for row in idx]
    codes = day_codes(trades)
    nd = int(codes.max()) + 1
    groups = [np.flatnonzero(codes == d) for d in range(nd)]
    H = horizon or nd
    draws = rng.integers(0, nd, size=(n_sims, H))
    out = []
    for row in draws:
        parts = [groups[d] for d in row]
        out.append((np.concatenate(parts), np.repeat(np.arange(H), [len(p) for p in parts])))
    return out


def _dist(x: np.ndarray) -> dict:
    x = np.asarray(x, dtype=float)
    if len(x) == 0 or not np.isfinite(x).any():
        return {k: float("nan") for k in ("mean", "sd", "p05", "p25", "p50", "p75", "p95")}
    q = np.nanquantile(x, [0.05, 0.25, 0.5, 0.75, 0.95])
    return {"mean": float(np.nanmean(x)), "sd": float(np.nanstd(x)), "p05": float(q[0]), "p25": float(q[1]),
            "p50": float(q[2]), "p75": float(q[3]), "p95": float(q[4])}


def account_feasibility(trades: pd.DataFrame, account_size: float, margin_per_contract_usd: float | None,
                        max_risk_fraction: float = 0.02) -> dict:
    """Documented constraint, not a forecast. Margin is broker-set and varies; supply it."""
    qty = int(trades["qty"].max()) if len(trades) and "qty" in trades else 1
    max_risk = float(trades["risk_usd"].max()) if len(trades) and "risk_usd" in trades else float("nan")
    notes = []
    if margin_per_contract_usd is None:
        margin_ok = None
        notes.append("margin_per_contract_usd not supplied: look up the CURRENT IBKR intraday/overnight margin "
                     "for the contract; nothing is assumed here.")
    else:
        need = qty * margin_per_contract_usd
        margin_ok = need <= account_size
        notes.append(f"margin needed for qty={qty}: ${need:,.2f} vs account ${account_size:,.2f}"
                     + ("" if margin_ok else " -> account cannot practically hold this position"))
    frac = max_risk / account_size if np.isfinite(max_risk) else float("nan")
    if np.isfinite(frac) and frac > max_risk_fraction:
        notes.append(f"max planned risk per trade ${max_risk:,.2f} = {frac:.1%} of account (> {max_risk_fraction:.0%}): "
                     "a short losing run can end the account regardless of edge.")
    return {"account_size": account_size, "max_qty": qty, "max_risk_usd": max_risk, "max_risk_fraction": frac,
            "margin_per_contract_usd": margin_per_contract_usd, "margin_ok": margin_ok, "notes": notes}


def run_montecarlo(trades: pd.DataFrame, cfg: MCConfig) -> dict:
    t = _prep(trades)
    t = apply_worse_stop(t, cfg.worse_stop_ticks, cfg.instrument)
    res = {"config": {k: (str(v) if k == "instrument" else v) for k, v in asdict(cfg).items()},
           "n_trades_input": len(t), "caveat": CAVEAT,
           "feasibility": account_feasibility(t, cfg.account_size, cfg.margin_per_contract_usd)}
    if len(t) == 0:
        res["note"] = "no trades: nothing to simulate"
        return res
    rng = np.random.default_rng(cfg.seed)
    pnl_all = t["pnl_net"].to_numpy(dtype=float)
    winner = pnl_all > 0
    paths = draw_paths(t, cfg.method, cfg.n_sims, cfg.horizon, cfg.block_len, rng)
    ev, tot, dd, streak, ruin, bdd, bday, ntr = [], [], [], [], [], [], [], []
    for idx, seg in paths:
        if cfg.fill_miss_frac > 0 and len(idx):
            keep = ~(winner[idx] & (rng.random(len(idx)) < cfg.fill_miss_frac))
            idx = idx[keep]
            seg = seg[keep] if seg is not None else None
        p = pnl_all[idx]
        ntr.append(len(p))
        ev.append(p.mean() if len(p) else np.nan)
        tot.append(p.sum())
        d, _ = max_drawdown(p, cfg.account_size)
        dd.append(d)
        streak.append(longest_losing_streak(p))
        ruin.append(bool(len(p) and (cfg.account_size + np.cumsum(p) <= cfg.ruin_equity).any()))
        bdd.append(cfg.max_dd_limit is not None and d >= cfg.max_dd_limit)
        if cfg.daily_loss_limit is not None and seg is not None:
            dp = np.bincount(seg, weights=p) if len(p) else np.array([0.0])
            bday.append(bool((dp <= -cfg.daily_loss_limit).any()))
    ev, tot, dd, streak = map(np.asarray, (ev, tot, dd, streak))
    res.update(
        method=cfg.method, n_sims=cfg.n_sims, seed=cfg.seed,
        observed={"ev_usd": float(pnl_all.mean()), "total_usd": float(pnl_all.sum())},
        distributions={"ev_per_trade_usd": _dist(ev), "total_pnl_usd": _dist(tot), "max_drawdown_usd": _dist(dd),
                       "longest_losing_streak": _dist(streak), "trades_per_path": _dist(np.asarray(ntr))},
        probabilities={
            "p_ev_le_0": float(np.nanmean(ev <= 0)), "p_total_pnl_lt_0": float((tot < 0).mean()),
            "p_ruin": float(np.mean(ruin)),
            "p_breach_max_dd_limit": float(np.mean(bdd)) if cfg.max_dd_limit is not None else None,
            "p_breach_daily_loss_limit": (float(np.mean(bday)) if bday else None),
        })
    if cfg.daily_loss_limit is not None and cfg.method != "day_block":
        res["probabilities"]["note_daily"] = "daily-loss breach only defined for method='day_block'"
    return res


def cost_stress(trades: pd.DataFrame, inst: Instrument, multipliers=(1.0, 1.5, 2.0), extra_ticks=(0.0, 1.0, 2.0),
                sides: int = 2, n_boot: int = 2000, seed: int = 0) -> pd.DataFrame:
    """Deterministic re-pricing: pnl' = pnl_gross - costs*m - extra_ticks*tick_usd*qty*sides
    (extra_ticks = additional adverse ticks PER SIDE per contract; sides=2 for a round trip).
    Recomputed from each trade's qty; prices/fills are NOT re-simulated. Adds a day-bootstrap CI."""
    t = _prep(trades)
    rows = []
    for m in multipliers:
        for k in extra_ticks:
            if len(t) == 0:
                rows.append({"cost_mult": m, "extra_ticks": k, "n": 0})
                continue
            extra = k * _tick_usd(inst) * t["qty"].to_numpy(dtype=float) * sides
            t2 = t.copy()
            t2["pnl_net"] = t["pnl_gross"].to_numpy(dtype=float) - t["costs"].to_numpy(dtype=float) * m - extra
            ci = bootstrap_mean_ci_by_day(t2, n_boot=n_boot, seed=seed)
            rows.append({"cost_mult": m, "extra_ticks": k, "n": len(t2), "ev_usd": float(t2["pnl_net"].mean()),
                         "total_usd": float(t2["pnl_net"].sum()), "ci_lo": ci["lo"], "ci_hi": ci["hi"],
                         "hit_rate": float((t2["pnl_net"] > 0).mean())})
    df = pd.DataFrame(rows)
    df.attrs["caveat"] = CAVEAT
    return df
