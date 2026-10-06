"""Shared contracts. OWNED BY THE COORDINATOR. Workers must not edit this file;
request changes from the coordinator instead.

Data formats
------------
Bars (pandas.DataFrame), one row per bar, sorted ascending, unique index:
  index   : tz-aware UTC DatetimeIndex named "ts_open" (bar OPEN time).
  columns : open, high, low, close (float64), volume (float64), plus
            "contract" (str, e.g. "MESZ4") for futures where known.
  A bar labelled ts_open=T covers [T, T + bar_seconds). It is only knowable at
  T + bar_seconds. Strategies may use bar i's values only when deciding at or
  after ts_open[i] + bar_seconds.
  Session logic uses America/New_York local time (convert at use, never store).

Trades (pandas.DataFrame) -- the interface between backtester and validation:
  TRADE_COLUMNS below. All timestamps tz-aware UTC. Money in account currency
  USD for the instrument (futures settle in USD). 'pnl_gross' excludes costs;
  'costs' is a positive number (commission+fees+spread+slippage in USD);
  'pnl_net' = pnl_gross - costs.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol
import pandas as pd

BAR_COLUMNS = ["open", "high", "low", "close", "volume"]
BAR_INDEX_NAME = "ts_open"

TRADE_COLUMNS = [
    "trade_id",        # int, unique
    "strategy",        # str, strategy/variant id (must match EXPERIMENT_LOG)
    "side",            # +1 long, -1 short
    "qty",             # int contracts
    "signal_ts",       # UTC ts the signal became knowable (bar close)
    "entry_ts",        # UTC ts of the fill
    "exit_ts",         # UTC ts of the exit fill
    "entry_px", "exit_px",
    "exit_reason",     # "stop" | "target" | "time" | "eod" | "ambiguous_stop"
    "risk_usd",        # planned initial risk in USD (qty * stop distance * pt value), before costs
    "pnl_gross", "costs", "pnl_net",
    "r_multiple",      # pnl_net / risk_usd
    "bars_held",
]

@dataclass(frozen=True)
class Instrument:
    symbol: str            # "MES"
    point_value: float     # USD per 1.0 index point (MES=5.0, ES=50.0, MNQ=2.0, NQ=20.0)
    tick_size: float       # 0.25
    currency: str = "USD"

@dataclass(frozen=True)
class CostModel:
    """All values are ASSUMPTIONS, to be documented/stressed, never presented as facts."""
    commission_per_side_usd: float   # per contract, broker commission
    exchange_fee_per_side_usd: float # per contract exchange+clearing+regulatory
    spread_ticks: float              # full quoted spread in ticks; half is paid on each market fill
    slippage_ticks_per_side: float   # extra adverse ticks per market/stop fill
    stop_extra_slippage_ticks: float = 1.0  # additional adverse ticks on stop fills
    cost_multiplier: float = 1.0     # stress knob: scales all of the above

@dataclass(frozen=True)
class Split:
    """Chronological split. Boundaries are UTC timestamps, half-open [start, end)."""
    train: tuple[pd.Timestamp, pd.Timestamp]
    validation: tuple[pd.Timestamp, pd.Timestamp]
    test: tuple[pd.Timestamp, pd.Timestamp]
    purge: pd.Timedelta   # gap removed before each later segment so trade outcomes cannot straddle a boundary

class Strategy(Protocol):
    name: str
    def generate_orders(self, bars: pd.DataFrame, params: dict) -> pd.DataFrame:
        """Return order intents using only information knowable at each decision time.
        Must not read bars after the decision time. Engine enforces by feeding data
        incrementally in the leakage test (see tests/test_no_lookahead.py)."""
        ...
