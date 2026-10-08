"""Daily open->close engine. Signal at close of day t -> fill at OPEN of t+1 -> exit at CLOSE of t+1.
Fixed notional sizing, fractional qty. All costs are ASSUMPTIONS (configs/daily_costs.yaml)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from tradelab.contracts import TRADE_COLUMNS
from tradelab.daily.signals import NY, bar_dates, signal_table

DAILY_TRADE_COLUMNS = TRADE_COLUMNS + ["symbol"]


@dataclass(frozen=True)
class DailyCosts:
    commission_per_order_usd: float = 0.35
    half_spread_usd_per_share_per_side: float = 0.005
    slippage_bps_open: float = 1.0
    slippage_bps_close: float = 0.5
    multiplier: float = 1.0

    def scaled(self, m: float) -> "DailyCosts":
        return DailyCosts(self.commission_per_order_usd, self.half_spread_usd_per_share_per_side,
                          self.slippage_bps_open, self.slippage_bps_close, m)

    def round_trip(self, qty, entry_px, exit_px):
        """Positive USD cost, vectorised."""
        qty, entry_px, exit_px = (np.asarray(x, dtype=float) for x in (qty, entry_px, exit_px))
        commission = 2 * self.commission_per_order_usd
        spread = 2 * self.half_spread_usd_per_share_per_side * qty
        slip = qty * entry_px * self.slippage_bps_open * 1e-4 + qty * exit_px * self.slippage_bps_close * 1e-4
        return self.multiplier * (commission + spread + slip)


def load_costs(path="configs/daily_costs.yaml") -> tuple[DailyCosts, dict]:
    cfg = yaml.safe_load(Path(path).read_text())
    return DailyCosts(**cfg["base"]), cfg.get("scenarios", {"x1": 1.0})


def _session_close_utc(dates: pd.Series) -> pd.Series:
    """16:00 New York on each date, in UTC (half-days close earlier: ignored, flagged)."""
    return (dates + pd.Timedelta(hours=16)).dt.tz_localize(NY).dt.tz_convert("UTC")


def candidates(bars: pd.DataFrame, symbol: str, notional: float = 10000.0, atr_n: int = 14,
               max_gap_days: int = 4) -> pd.DataFrame:
    """One row per ELIGIBLE signal day t (regardless of whether a signal fires): the t+1 open->close
    opportunity with qty, risk_usd and timestamps. Used by the engine and the baselines."""
    sig = signal_table(bars, atr_n, max_gap_days)
    dates = bar_dates(bars)
    nxt = bars.shift(-1)
    df = pd.DataFrame({
        "symbol": symbol, "signal": sig["signal"], "eligible": sig["eligible"], "atr": sig["atr"],
        "signal_ts": _session_close_utc(dates),
        "entry_ts": pd.Series(bars.index, index=bars.index).shift(-1),
        "exit_ts": _session_close_utc(dates.shift(-1).fillna(dates)),
        "entry_px": nxt["open"], "exit_px": nxt["close"],
    })
    df = df[df["eligible"]].copy()
    df["qty"] = notional / df["entry_px"]
    df["risk_usd"] = df["qty"] * df["atr"]
    return df.reset_index(drop=True)


def make_trades(cands: pd.DataFrame, sides, strategy: str, costs: DailyCosts, start_id: int = 0) -> pd.DataFrame:
    """Build trades from candidate rows. `sides`: +1/-1 scalar or per-row array."""
    n = len(cands)
    side = np.broadcast_to(np.asarray(sides), (n,)).astype(int)
    entry, exit_ = cands["entry_px"].to_numpy(float), cands["exit_px"].to_numpy(float)
    qty = cands["qty"].to_numpy(float)
    gross = side * (exit_ - entry) * qty
    cost = costs.round_trip(qty, entry, exit_)
    net = gross - cost
    risk = cands["risk_usd"].to_numpy(float)
    out = pd.DataFrame({
        "trade_id": np.arange(start_id, start_id + n), "strategy": strategy, "side": side, "qty": qty,
        "signal_ts": cands["signal_ts"].to_numpy(), "entry_ts": cands["entry_ts"].to_numpy(),
        "exit_ts": cands["exit_ts"].to_numpy(), "entry_px": entry, "exit_px": exit_, "exit_reason": "eod",
        "risk_usd": risk, "pnl_gross": gross, "costs": cost, "pnl_net": net,
        "r_multiple": net / risk, "bars_held": 1, "symbol": cands["symbol"].to_numpy(),
    })
    for c in ("signal_ts", "entry_ts", "exit_ts"):
        out[c] = pd.to_datetime(out[c], utc=True)
    return out[DAILY_TRADE_COLUMNS]


def variant_sides(cands: pd.DataFrame, which: str) -> pd.DataFrame:
    s = cands["signal"]
    keep = {"both": s != 0, "long": s == 1, "short": s == -1}[which]
    return cands[keep]


def strategy_id(variant: str, universe) -> str:
    return f"{variant}@universe={'|'.join(universe)}"


def run_variant(bars_by_symbol: dict, variant: str, which: str, costs: DailyCosts, notional: float = 10000.0,
                atr_n: int = 14, max_gap_days: int = 4) -> pd.DataFrame:
    sid = strategy_id(variant, list(bars_by_symbol))
    parts = []
    for sym, bars in bars_by_symbol.items():
        c = variant_sides(candidates(bars, sym, notional, atr_n, max_gap_days), which)
        parts.append(make_trades(c, c["signal"].to_numpy(), sid, costs))
    if not parts:
        return pd.DataFrame(columns=DAILY_TRADE_COLUMNS)
    t = pd.concat(parts, ignore_index=True).sort_values(["entry_ts", "symbol"], kind="stable").reset_index(drop=True)
    t["trade_id"] = np.arange(len(t))
    return t
