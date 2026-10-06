"""Order-intent record passed from a streaming strategy to the engine."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol
import pandas as pd


@dataclass(frozen=True)
class Signal:
    """Market entry decided at the close of a bar; filled at the next bar's open.

    side: +1 long / -1 short.  stop_px: resting stop price (fixed at decision).
    ref_px: signal bar close (only for the decision-time risk filter, already applied by strategy).
    min/max_risk_pts: engine re-checks |fill_open - stop| against these at fill time.
    time_exit_min: ET minutes since midnight at/after which the position is closed at a bar open.
    """
    strategy_id: str
    side: int
    signal_ts: pd.Timestamp        # UTC; the time the signal became knowable (signal bar close)
    stop_px: float
    ref_px: float
    target_r: float
    min_risk_pts: float
    max_risk_pts: float
    time_exit_min: int
    meta: dict[str, Any] = field(default_factory=dict)


class StreamingStrategy(Protocol):
    """Strategies see ONE completed bar at a time; look-ahead is impossible by construction."""
    name: str

    def reset(self) -> None: ...

    def on_bar(self, ts_open: pd.Timestamp, o: float, h: float, l: float, c: float,
               v: float, contract: str | None) -> Signal | None: ...
