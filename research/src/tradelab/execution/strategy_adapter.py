"""One Strategy interface for backtest / replay / shadow / execution: on_bar(bar) -> Signal|None (contracts.Strategy).
The strategy gets bars and nothing else: no service, no mode, no broker, no risk engine reference."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

import pandas as pd

from tradelab.execution.common import D
from tradelab.execution.intents import ENTRY, Proposal


@dataclass(frozen=True)
class InstrumentSpec:
    con_id: int
    symbol: str


def signal_id_of(sig) -> str:
    ts = pd.Timestamp(sig.signal_ts).tz_convert("UTC").isoformat()
    return f"{sig.strategy_id}:{ts}:{int(sig.side)}"


class SignalTranslator:
    """Signal (backtest contract) -> Proposal. `sizer(signal)` returns the quantity; it is the ONLY place sizing lives and the risk
    engine still has the last word."""

    def __init__(self, inst: InstrumentSpec, strategy_version: str, sizer: Callable, order_type: str = "MKT"):
        self.inst, self.version, self.sizer, self.order_type = inst, strategy_version, sizer, order_type

    def entry(self, sig) -> Proposal:
        stop = getattr(sig, "stop_px", None)
        return Proposal(strategy_id=sig.strategy_id, strategy_version=self.version, signal_id=signal_id_of(sig), con_id=self.inst.con_id,
                        symbol=self.inst.symbol, side=int(sig.side), qty=D(self.sizer(sig)), order_type=self.order_type,
                        stop_px=None if stop is None else D(stop), purpose=ENTRY)
