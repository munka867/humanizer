"""Summaries for the daily study. Everything is NET of costs; inference is day-clustered (validation.stats)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradelab.contracts import Instrument
from tradelab.validation import montecarlo as MC
from tradelab.validation import stats as ST

EQUITY = 10000.0
_INST = Instrument("ETF", 1.0, 0.01)   # montecarlo.cost_stress requires an Instrument even with extra_ticks=0


def load_bars(symbol: str, data_dir="data/processed") -> pd.DataFrame:
    df = pd.read_csv(f"{data_dir}/{symbol}_1d.csv")
    df["ts_open"] = pd.to_datetime(df["ts_open"], utc=True)
    df = df.set_index("ts_open").sort_index()
    if not df.index.is_unique:
        raise ValueError(f"{symbol}: duplicate timestamps")
    return df


def bps_of(t: pd.DataFrame) -> np.ndarray:
    return (t["pnl_net"] / (t["qty"] * t["entry_px"])).to_numpy(float) * 1e4


def summarize(t: pd.DataFrame, n_days: int, seed: int, n_variants: int, n_boot: int = 5000) -> dict:
    n = len(t)
    if n == 0:
        return {"n": 0}
    m = ST.metrics(t, EQUITY, n_variants_tried=n_variants, n_boot=n_boot, seed=seed, n_session_days=max(1, n_days),
                   include_periods=False)
    pnl = t["pnl_net"].to_numpy(float)
    win, loss = pnl[pnl > 0], pnl[pnl < 0]
    top5 = float(np.sort(pnl)[::-1][:5].sum() / pnl.sum()) if pnl.sum() > 0 else float("nan")  # undefined when total <= 0
    entry_days = t["entry_ts"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d").nunique()
    out = {"n": n, "n_days_with_trades": int(entry_days), "ev_usd": m["ev_usd"], "ev_r": m["ev_r"],
           "ev_bps": float(bps_of(t).mean()), "hit": m["hit_rate"], "avg_win": float(win.mean()) if len(win) else float("nan"),
           "avg_loss": float(loss.mean()) if len(loss) else float("nan"), "pf": m["profit_factor"],
           "maxdd_usd": m["max_dd_usd"], "exposure_days": entry_days / max(1, n_days), "exposure_time": m["exposure"],
           "total_usd": m["total_pnl_net"], "ci_usd": m["boot_ci_ev_usd"], "ci_r": m["boot_ci_ev_r"],
           "p_day_one_sided": m["p_day_one_sided"], "p_day_two_sided": m["p_day_two_sided"],
           "p_bonferroni_day": m["p_bonferroni_day"], "dsr": m["deflated_sharpe"].get("dsr"),
           "dsr_detail": m["deflated_sharpe"], "top5_share": top5, "longest_losing_streak": m["longest_losing_streak"]}
    return out


def long_short(t: pd.DataFrame) -> dict:
    out = {}
    for name, s in (("long", 1), ("short", -1)):
        x = t[t["side"] == s]
        out[name] = {"n": len(x), "ev_usd": float(x["pnl_net"].mean()) if len(x) else None,
                     "ev_bps": float(bps_of(x).mean()) if len(x) else None,
                     "ev_r": float(x["r_multiple"].mean()) if len(x) else None,
                     "hit": float((x["pnl_net"] > 0).mean()) if len(x) else None}
    return out


def cost_table(t: pd.DataFrame, seed: int, n_boot: int = 5000) -> list[dict]:
    if len(t) == 0:
        return []
    cs = MC.cost_stress(t, _INST, multipliers=(1.0, 1.5, 2.0), extra_ticks=(0.0,), n_boot=n_boot, seed=seed)
    return cs.to_dict("records")


def quarter_table(t: pd.DataFrame) -> list[dict]:
    return ST.period_breakdown(t, "quarter").to_dict("records") if len(t) else []


def month_max_share(t: pd.DataFrame) -> float | None:
    if len(t) == 0:
        return None
    mb = ST.period_breakdown(t, "month")
    tot = mb["pnl_net_sum"].sum()
    return float(mb["share_of_total"].max()) if tot > 0 else None   # undefined/fails when total P&L <= 0


def positive_quarter_fraction(t: pd.DataFrame, min_trades: int = 10) -> float | None:
    q = ST.period_breakdown(t, "quarter") if len(t) else pd.DataFrame()
    q = q[q["n"] >= min_trades] if len(q) else q
    return float((q["pnl_net_mean"] > 0).mean()) if len(q) else None


def monte_carlo(t: pd.DataFrame, seed: int, n_sims: int, method: str = "day_block", account: float = EQUITY) -> dict:
    if len(t) == 0:
        return {"note": "no trades"}
    r = MC.run_montecarlo(t, MC.MCConfig(seed=seed, method=method, n_sims=n_sims, account_size=account))
    return {"method": method, "seed": seed, "n_sims": n_sims, "distributions": r["distributions"],
            "probabilities": r["probabilities"], "caveat": r["caveat"]}
