"""Descriptive trade metrics (spec section 7). Inference (CIs, MC) belongs to validation/."""
from __future__ import annotations
import numpy as np
import pandas as pd

RTH_MINUTES = 390


def summarize(trades: pd.DataFrame, n_trading_days: int | None = None, bar_minutes: int = 5) -> dict:
    n = len(trades)
    if n == 0:
        return {"n_trades": 0}
    t = trades.sort_values("exit_ts")
    net = t["pnl_net"].to_numpy(float)
    wins, losses = net[net > 0].sum(), -net[net < 0].sum()
    eq = np.cumsum(net)
    dd = float((np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max())
    out = {
        "n_trades": n, "net_ev_usd": float(net.mean()), "net_ev_r": float(t["r_multiple"].mean()),
        "profit_factor": float(wins / losses) if losses > 0 else float("inf"),
        "max_drawdown_usd": dd, "total_net_usd": float(net.sum()),
        "total_costs_usd": float(t["costs"].sum()),
        "exit_reason_counts": t["exit_reason"].value_counts().to_dict(),
    }
    if n_trading_days:
        out["exposure_frac_of_rth"] = float(t["bars_held"].sum() * bar_minutes / (n_trading_days * RTH_MINUTES))
        out["days_traded_frac"] = float(t["entry_ts"].dt.tz_convert("America/New_York").dt.date.nunique() / n_trading_days)
    return out
