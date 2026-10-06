"""H1: prior-extreme liquidity sweep reversal (see docs/STRATEGY_SPEC.md section 3). Streaming/causal."""
from __future__ import annotations
import datetime as dt
from collections import Counter
from dataclasses import dataclass
import pandas as pd
from tradelab.contracts import Instrument
from tradelab.backtest.sessions import BAR_MIN, RTH_START, SessionTracker, hhmm
from tradelab.backtest.signals import Signal

LEVEL_SETS = ("prev_rth", "overnight", "both")


@dataclass(frozen=True)
class SweepParams:
    target_r: float = 2.0
    levels: str = "both"
    min_sweep_ticks: int = 1
    stop_buffer_ticks: int = 1
    first_signal_bar: str = "09:35"      # first bar (09:30) never a signal bar
    last_signal_bar: str = "10:55"       # bar OPEN time; closes 11:00
    time_exit: str = "11:30"
    min_risk_pts: float = 2.0
    max_risk_pts: float = 12.0


class SweepReversal:
    def __init__(self, instrument: Instrument, params: SweepParams = SweepParams(),
                 strategy_id: str = "H1_sweep_reversal", require_contract: bool = True):
        if params.levels not in LEVEL_SETS:
            raise ValueError(f"levels must be one of {LEVEL_SETS}")
        self.inst, self.p, self.name = instrument, params, strategy_id
        self._first, self._last = hhmm(params.first_signal_bar), hhmm(params.last_signal_bar)
        if self._first <= RTH_START:
            raise ValueError("first signal bar must be after the 09:30 bar (spec 3.2)")
        self._exit = hhmm(params.time_exit)
        self._tracker = SessionTracker(require_contract=require_contract)
        self.reset()

    def reset(self) -> None:
        self._tracker.reset()
        self._used_day: dt.date | None = None
        self._levels: dict | None = None
        self._levels_day: dt.date | None = None
        self.diagnostics: Counter = Counter()

    def _freeze_levels(self, ctx) -> None:
        """Levels are fixed at the first RTH bar after 09:30 (inputs complete by 09:30)."""
        lv = {}
        if self.p.levels in ("prev_rth", "both"):
            lv["prev_hi"], lv["prev_lo"] = ctx.prev.hi, ctx.prev.lo
        if self.p.levels in ("overnight", "both"):
            lv["on_hi"], lv["on_lo"] = ctx.on_hi, ctx.on_lo
        self._levels, self._levels_day = lv, ctx.date

    def on_bar(self, ts_open, o, h, l, c, v, contract) -> Signal | None:
        ctx = self._tracker.update(ts_open, h, l, contract)
        if ctx.phase != "RTH" or ctx.tod < self._first or ctx.tod > self._last:
            return None
        if self._used_day == ctx.date:
            return None
        need_prev = self.p.levels in ("prev_rth", "both")
        need_on = self.p.levels in ("overnight", "both")
        reason = ctx.skip_reason(need_prev, need_on)
        if reason is not None:
            self.diagnostics["skip_" + reason] += 1
            return None
        if self._levels_day != ctx.date:
            self._freeze_levels(ctx)
        tick = self.inst.tick_size
        eps = 1e-9
        k = self.p.min_sweep_ticks * tick
        highs = [L for n, L in self._levels.items() if n.endswith("_hi")]
        lows = [L for n, L in self._levels.items() if n.endswith("_lo")]
        short_lv = [L for L in highs if h >= L + k - eps and c < L]
        long_lv = [L for L in lows if l <= L - k + eps and c > L]
        if short_lv and long_lv:
            self.diagnostics["both_sides_same_bar"] += 1
            return None
        if not short_lv and not long_lv:
            return None
        self._used_day = ctx.date  # first qualifying signal consumes the day
        side = -1 if short_lv else 1
        stop = h + self.p.stop_buffer_ticks * tick if side == -1 else l - self.p.stop_buffer_ticks * tick
        d_sig = abs(stop - c)
        if d_sig < self.p.min_risk_pts - eps or d_sig > self.p.max_risk_pts + eps:
            self.diagnostics["rejected_risk_filter"] += 1
            return None
        self.diagnostics["signals"] += 1
        return Signal(
            strategy_id=self.name, side=side, signal_ts=ts_open + pd.Timedelta(minutes=BAR_MIN),
            stop_px=stop, ref_px=c, target_r=self.p.target_r,
            min_risk_pts=self.p.min_risk_pts, max_risk_pts=self.p.max_risk_pts,
            time_exit_min=self._exit,
            meta={"level": max(short_lv) if side == -1 else min(long_lv)})
